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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ICCID,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_SIM_NUMBER,
    ATTR_TOTAL,
    ATTR_TOTAL_GBR,
    ATTR_TOTAL_GLOBAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_GBR,
    ATTR_USAGE_GLOBAL,
    ATTR_USAGE_PERCENTAGE,
)
from .coordinator import MozillionCoordinator
from .entity import MozillionEntity

# Mozillion reports separate UK ("Gbr") and roaming ("Global") buckets alongside
# the headline figure. Which one the headline tracks is unverified, so the
# buckets are exposed as attributes rather than entities of their own.


@dataclass(frozen=True, kw_only=True)
class MozillionSensorEntityDescription(SensorEntityDescription):
    """Describe a Mozillion sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mozillion sensors from config entry."""

    coordinator = entry.runtime_data.coordinator

    async_add_entities(
        MozillionSensor(coordinator, entry, description) for description in DATA_SENSORS
    )


class MozillionSensor(MozillionEntity, SensorEntity):
    """Representation of a Mozillion sensor."""

    entity_description: MozillionSensorEntityDescription

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        description: MozillionSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw payload plus the per-bucket usage breakdown."""

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
        }
