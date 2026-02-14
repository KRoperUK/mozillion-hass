"""Sensor platform for Mozillion data usage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)
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


@dataclass(frozen=True, kw_only=True)
class MozillionSensorEntityDescription(SensorEntityDescription):
    """Describe a Mozillion sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


DATA_SENSORS: tuple[MozillionSensorEntityDescription, ...] = (
    MozillionSensorEntityDescription(
        key=ATTR_USAGE,
        translation_key="usage",
        name="Usage",
        icon="mdi:sim",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get(ATTR_USAGE),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_TOTAL,
        translation_key="total",
        name="Total",
        icon="mdi:sim-outline",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get(ATTR_TOTAL),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_REMAINING,
        translation_key="remaining",
        name="Remaining",
        icon="mdi:sim-outline",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get(ATTR_REMAINING),
    ),
    MozillionSensorEntityDescription(
        key=ATTR_USAGE_PERCENTAGE,
        translation_key="usage_percentage",
        name="Usage Percentage",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            round(data.get(ATTR_USAGE_PERCENTAGE), 2)
            if data.get(ATTR_USAGE_PERCENTAGE) is not None
            else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Mozillion sensors from config entry."""

    coordinator: MozillionCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sim_number = entry.data.get(CONF_SIM_NUMBER, "")

    async_add_entities(
        MozillionSensor(coordinator, entry, sim_number, description)
        for description in DATA_SENSORS
    )


class MozillionSensor(CoordinatorEntity[MozillionCoordinator], SensorEntity):
    """Representation of a Mozillion sensor."""

    _attr_has_entity_name = True
    entity_description: MozillionSensorEntityDescription

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        sim_number: str,
        description: MozillionSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sim_number or entry.entry_id)},
            name=f"Mozillion {sim_number}" if sim_number else "Mozillion",
            manufacturer="Mozillion",
            suggested_area="Network",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return raw payload as attribute."""
        return {ATTR_RAW: self.coordinator.data.get(ATTR_RAW)}
