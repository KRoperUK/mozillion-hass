"""Sensor platform for Mozillion data usage."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_TOTAL,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    CONF_SIM_NUMBER,
    DOMAIN,
)
from . import MozillionCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Mozillion sensors from config entry."""

    coordinator: MozillionCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sim_number = entry.data.get(CONF_SIM_NUMBER, "")

    entities: list[SensorEntity] = [
        MozillionDataSensor(coordinator, entry, sim_number, ATTR_USAGE, "Usage", "mdi:sim"),
        MozillionDataSensor(coordinator, entry, sim_number, ATTR_TOTAL, "Total", "mdi:sim-outline"),
        MozillionDataSensor(coordinator, entry, sim_number, ATTR_REMAINING, "Remaining", "mdi:sim-outline"),
        MozillionPercentageSensor(coordinator, entry, sim_number),
    ]

    async_add_entities(entities)


class MozillionDataSensor(CoordinatorEntity[MozillionCoordinator], SensorEntity):
    """Representation of a Mozillion data sensor (usage, total, remaining)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        sim_number: str,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sim_number or entry.entry_id)},
            name=f"Mozillion {sim_number}" if sim_number else "Mozillion",
            manufacturer="Mozillion",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.coordinator.data.get(self._key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return raw payload as attribute."""
        return {ATTR_RAW: self.coordinator.data.get(ATTR_RAW)}


class MozillionPercentageSensor(CoordinatorEntity[MozillionCoordinator], SensorEntity):
    """Representation of Mozillion usage percentage sensor."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:percent"

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        sim_number: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_usage_percentage"
        self._attr_name = "Usage Percentage"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sim_number or entry.entry_id)},
            name=f"Mozillion {sim_number}" if sim_number else "Mozillion",
            manufacturer="Mozillion",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        value = self.coordinator.data.get(ATTR_USAGE_PERCENTAGE)
        if value is not None:
            return round(value, 2)
        return None
