"""Shared entity behaviour for the Mozillion integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ICCID, CONF_SIM_META_ID, CONF_SIM_NUMBER, DOMAIN
from .coordinator import MozillionCoordinator


def sim_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the device registry metadata for the tracked SIM.

    The SIM's ICCID identifies the physical card, so it is the device's serial
    number; ``sim_meta_id`` is Mozillion's own id and is the stable key the
    usage endpoints are polled with.
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
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = sim_device_info(entry)
