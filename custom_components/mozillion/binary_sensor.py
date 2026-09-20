"""Binary sensor platform for Mozillion data usage."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_UNLIMITED, ATTR_USAGE, ATTR_USAGE_PERCENTAGE
from .coordinator import MozillionCoordinator
from .entity import MozillionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mozillion binary sensors from config entry."""

    coordinator = entry.runtime_data.coordinator

    async_add_entities([MozillionUnlimitedSensor(coordinator, entry)])


class MozillionUnlimitedSensor(MozillionEntity, BinarySensorEntity):
    """Representation of Mozillion unlimited boolean sensor."""

    _attr_translation_key = "unlimited"

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the unlimited sensor."""
        super().__init__(coordinator, entry, ATTR_UNLIMITED)

    @property
    def is_on(self) -> bool:
        """Return True when the plan has no data cap."""
        return bool(self.coordinator.data.get(ATTR_UNLIMITED))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the figures the unlimited flag is derived from."""

        data = self.coordinator.data
        return {
            ATTR_USAGE: data.get(ATTR_USAGE),
            ATTR_USAGE_PERCENTAGE: data.get(ATTR_USAGE_PERCENTAGE),
        }
