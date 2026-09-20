"""Shared entity behaviour for the Mozillion integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_PLAN_TARIFF,
    CONF_ICCID,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    DOMAIN,
)
from .coordinator import MozillionCoordinator


def sim_device_info(entry: ConfigEntry, plan_tariff: str | None = None) -> DeviceInfo:
    """Return the device registry metadata for the tracked SIM.

    The SIM's ICCID identifies the physical card, so it is the device's serial
    number; ``sim_meta_id`` is Mozillion's own id and is the stable key the
    usage endpoints are polled with. The plan tariff is only known once the
    dashboard has been read, and the device is renamed if the plan changes.
    """

    sim_number = entry.data.get(CONF_SIM_NUMBER, "")
    iccid = entry.data.get(CONF_ICCID, "")

    info = DeviceInfo(
        identifiers={(DOMAIN, entry.data.get(CONF_SIM_META_ID) or entry.entry_id)},
        name=f"Mozillion {sim_number}" if sim_number else "Mozillion",
        manufacturer="Mozillion",
        suggested_area="Network",
    )
    if iccid:
        info["serial_number"] = iccid
    if plan_tariff:
        info["model"] = plan_tariff
    return info


class MozillionEntity(CoordinatorEntity[MozillionCoordinator]):
    """Base entity wiring the coordinator to the SIM device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialize the entity and its device."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device, with the plan tariff refreshed each poll."""

        return sim_device_info(
            self._entry, self.coordinator.data.get(ATTR_PLAN_TARIFF) or None
        )
