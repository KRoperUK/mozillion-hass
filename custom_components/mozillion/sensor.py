"""Sensor platform for Mozillion data usage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MozillionConfigEntry
from .const import (
    ATTR_BILLING_AMOUNT,
    ATTR_BILLING_DAYS,
    ATTR_DAYS_LEFT,
    ATTR_HAS_BILL,
    ATTR_ICCID,
    ATTR_PLAN_DURATION,
    ATTR_PLAN_IS_DATA_ONLY,
    ATTR_PLAN_PARENTAL_CONTROL,
    ATTR_PLAN_ROAMING,
    ATTR_PLAN_TARIFF,
    ATTR_PLAN_TEXTS,
    ATTR_PLAN_VOICEMAIL,
    ATTR_PORT_DATE,
    ATTR_PORT_STATUS,
    ATTR_PORT_STATUS_DESCRIPTION,
    ATTR_PORT_STATUS_LABEL,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_RESET_DATE,
    ATTR_RESET_LABEL,
    ATTR_SIM_NUMBER,
    ATTR_SIM_STATUS,
    ATTR_TOTAL,
    ATTR_TOTAL_GBR,
    ATTR_TOTAL_GLOBAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_GBR,
    ATTR_USAGE_GLOBAL,
    ATTR_USAGE_PERCENTAGE,
    ATTR_WALLET,
    ATTR_WALLET_BALANCE,
    ATTR_WALLET_SPEND,
    SUBENTRY_TYPE_SIM,
)
from .coordinator import MozillionCoordinator, wallet_is_active
from .entity import MozillionEntity

# Mozillion reports separate UK ("Gbr") and roaming ("Global") buckets alongside
# the headline figure. Which one the headline tracks is unverified, so the
# buckets are exposed as attributes rather than entities of their own.
#
# The wallet figures come from the dashboard's overspend endpoint. A SIM with no
# wallet answers all zeros, so those entities report unavailable unless the
# wallet is actually in use (see `wallet_is_active`).
#
# GBP is an assumption, not something the endpoint states: Mozillion is a UK
# service and the site's own monetary attributes are in pounds, but the endpoint
# gives no currency and no scale, and a never-topped-up wallet reads zero, so the
# scale has never been checked against a real figure. Noted under known
# limitations in docs/index.md.


@dataclass(frozen=True, kw_only=True)
class MozillionSensorEntityDescription(SensorEntityDescription):
    """Describe a Mozillion sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    # Extra guard for entities that are only meaningful in some states.
    available_fn: Callable[[dict[str, Any]], bool] | None = None


DATA_SENSORS: tuple[MozillionSensorEntityDescription, ...] = (
    MozillionSensorEntityDescription(
        key=ATTR_USAGE,
        translation_key="usage",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get(ATTR_USAGE),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_TOTAL,
        translation_key="total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get(ATTR_TOTAL),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_REMAINING,
        translation_key="remaining",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get(ATTR_REMAINING),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_USAGE_PERCENTAGE,
        translation_key="usage_percentage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            round(pct, 2)
            if (pct := data.get(ATTR_USAGE_PERCENTAGE)) is not None
            else None
        ),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_RESET_DATE,
        translation_key="reset_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda data: data.get(ATTR_RESET_DATE),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_WALLET_BALANCE,
        translation_key="wallet_balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="GBP",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data.get(ATTR_WALLET_BALANCE),
        available_fn=wallet_is_active,
    ),
    MozillionSensorEntityDescription(
        key=ATTR_WALLET_SPEND,
        translation_key="wallet_spend",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="GBP",
        # Monetary readings only accept the TOTAL state class, and Mozillion's
        # spend figure is a per-period total rather than a running meter.
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data.get(ATTR_WALLET_SPEND),
        available_fn=wallet_is_active,
    ),
    MozillionSensorEntityDescription(
        key=ATTR_PORT_STATUS,
        translation_key="port_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        # The page's own wording rather than a status the integration would have
        # to invent: Mozillion uses values this integration has not seen, so the
        # label is shown and the raw value stays in the attributes.
        value_fn=lambda data: (
            data.get(ATTR_PORT_STATUS_LABEL) or data.get(ATTR_PORT_STATUS) or None
        ),
        # A SIM whose number was never ported reports nothing, and an entity
        # stuck on unknown would be noise -- the same treatment the wallet
        # figures get.
        available_fn=lambda data: bool(data.get(ATTR_PORT_STATUS)),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_SIM_STATUS,
        translation_key="sim_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get(ATTR_SIM_STATUS) or None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MozillionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a sensor set for every SIM on the account."""

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SIM:
            continue

        coordinator = entry.runtime_data.coordinators[subentry.subentry_id]
        async_add_entities(
            (
                MozillionSensor(coordinator, entry, subentry, description)
                for description in DATA_SENSORS
            ),
            config_subentry_id=subentry.subentry_id,
        )


class MozillionSensor(MozillionEntity, SensorEntity):
    """Representation of a Mozillion sensor."""

    entity_description: MozillionSensorEntityDescription

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        description: MozillionSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, subentry, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return False when this reading does not apply right now."""
        if not super().available:
            return False
        if self.entity_description.available_fn is None:
            return True
        return self.entity_description.available_fn(self.coordinator.data)

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw payloads plus the derived detail."""

        data = self.coordinator.data
        return {
            ATTR_RAW: data.get(ATTR_RAW),
            ATTR_SIM_NUMBER: data.get(ATTR_SIM_NUMBER),
            ATTR_ICCID: data.get(ATTR_ICCID),
            ATTR_UNLIMITED: data.get(ATTR_UNLIMITED),
            ATTR_USAGE_GBR: data.get(ATTR_USAGE_GBR),
            ATTR_TOTAL_GBR: data.get(ATTR_TOTAL_GBR),
            ATTR_USAGE_GLOBAL: data.get(ATTR_USAGE_GLOBAL),
            ATTR_TOTAL_GLOBAL: data.get(ATTR_TOTAL_GLOBAL),
            ATTR_RESET_LABEL: data.get(ATTR_RESET_LABEL),
            ATTR_DAYS_LEFT: data.get(ATTR_DAYS_LEFT),
            ATTR_PLAN_TARIFF: data.get(ATTR_PLAN_TARIFF),
            ATTR_PLAN_DURATION: data.get(ATTR_PLAN_DURATION),
            ATTR_PLAN_ROAMING: data.get(ATTR_PLAN_ROAMING),
            ATTR_PLAN_TEXTS: data.get(ATTR_PLAN_TEXTS),
            ATTR_PLAN_IS_DATA_ONLY: data.get(ATTR_PLAN_IS_DATA_ONLY),
            ATTR_PLAN_VOICEMAIL: data.get(ATTR_PLAN_VOICEMAIL),
            ATTR_PLAN_PARENTAL_CONTROL: data.get(ATTR_PLAN_PARENTAL_CONTROL),
            ATTR_PORT_STATUS: data.get(ATTR_PORT_STATUS),
            ATTR_PORT_STATUS_DESCRIPTION: data.get(ATTR_PORT_STATUS_DESCRIPTION),
            ATTR_PORT_DATE: data.get(ATTR_PORT_DATE),
            # Raw, with no unit claimed: the page states neither a currency nor
            # whether this is pence, and only says "Paid in full / No upcoming
            # bill" beside it.
            ATTR_BILLING_AMOUNT: data.get(ATTR_BILLING_AMOUNT),
            ATTR_HAS_BILL: data.get(ATTR_HAS_BILL),
            ATTR_BILLING_DAYS: data.get(ATTR_BILLING_DAYS),
            ATTR_WALLET: data.get(ATTR_WALLET),
        }
