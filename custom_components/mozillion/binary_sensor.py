"""Binary sensor platform for Mozillion data usage."""

from __future__ import annotations


from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_UNLIMITED, CONF_SIM_NUMBER, DOMAIN
from . import MozillionCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Mozillion binary sensors from config entry."""

    coordinator: MozillionCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sim_number = entry.data.get(CONF_SIM_NUMBER, "")

    entities: list[BinarySensorEntity] = [
        MozillionUnlimitedSensor(coordinator, entry, sim_number),
    ]

    async_add_entities(entities)


class MozillionUnlimitedSensor(
    CoordinatorEntity[MozillionCoordinator], BinarySensorEntity
):  # type: ignore[misc]
    """Representation of Mozillion unlimited boolean sensor."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:infinity"

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        sim_number: str,
    ) -> None:
        """Initialize the unlimited sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_unlimited"
        self._attr_name = "Unlimited"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sim_number or entry.entry_id)},
            name=f"Mozillion {sim_number}" if sim_number else "Mozillion",
            manufacturer="Mozillion",
            suggested_area="Network",
        )
        # Initialize the state
        self._attr_is_on = False

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = bool(self.coordinator.data.get(ATTR_UNLIMITED))
        super()._handle_coordinator_update()
