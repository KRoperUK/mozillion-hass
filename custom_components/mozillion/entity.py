"""Shared entity behaviour for the Mozillion integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import mask_identifier
from .const import (
    ATTR_PLAN_TARIFF,
    CONF_ICCID,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    DOMAIN,
)
from .coordinator import MozillionCoordinator


def _device_label(sim_number: str, plan_tariff: str | None) -> str:
    """Return what the device is named after, without the phone number.

    The name reaches every entity's ``friendly_name`` and every entity id on a
    fresh install, so it must not carry the number in full. The plan tariff says
    more to a user than a number does; the masked number is the fallback until the
    dashboard has been read, and users can rename the device if they want to.
    """

    if plan_tariff:
        return plan_tariff
    return mask_identifier(sim_number)


def sim_device_info(
    entry: ConfigEntry,
    subentry: ConfigSubentry,
    plan_tariff: str | None = None,
) -> DeviceInfo:
    """Return the device registry metadata for one SIM.

    ``sim_meta_id`` is Mozillion's own identifier for the SIM and the stable key the
    usage endpoints are polled with, so it still anchors the device after the hub
    restructure: an existing installation keeps the device it already has rather than
    gaining a second one. The ICCID identifies the physical card, so it is the serial.
    The plan tariff is only known once the dashboard has been read.
    """

    sim_number = subentry.data.get(CONF_SIM_NUMBER, "")
    iccid = subentry.data.get(CONF_ICCID, "")
    label = _device_label(sim_number, plan_tariff)

    info = DeviceInfo(
        identifiers={
            (DOMAIN, subentry.data.get(CONF_SIM_META_ID) or subentry.subentry_id)
        },
        name=f"Mozillion {label}" if label else "Mozillion",
        manufacturer="Mozillion",
        suggested_area="Network",
    )
    if iccid:
        info["serial_number"] = iccid
    if plan_tariff:
        info["model"] = plan_tariff
    return info


class MozillionEntity(CoordinatorEntity[MozillionCoordinator]):
    """Base entity wiring one SIM's coordinator to that SIM's device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        key: str,
    ) -> None:
        """Initialize the entity and its device."""
        super().__init__(coordinator)
        self._entry = entry
        self._subentry = subentry
        # Scoped by subentry, so a second SIM on the same account cannot collide
        # with the first.
        self._attr_unique_id = f"{entry.entry_id}_{subentry.subentry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device, with the plan tariff refreshed each poll."""

        return sim_device_info(
            self._entry, self._subentry, self.coordinator.data.get(ATTR_PLAN_TARIFF)
        )
