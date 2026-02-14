"""Tests for the Mozillion sensor and binary sensor platforms."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfInformation

from custom_components.mozillion.sensor import (
    DATA_SENSORS,
    MozillionSensor,
    MozillionSensorEntityDescription,
)
from custom_components.mozillion.binary_sensor import MozillionUnlimitedSensor
from custom_components.mozillion.const import (
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_TOTAL,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    DOMAIN,
)

from tests.conftest import (
    MOCK_COORDINATOR_DATA,
    MOCK_COORDINATOR_DATA_UNLIMITED,
    _make_config_entry,
)


# ---------------------------------------------------------------------------
# Sensor entity descriptions
# ---------------------------------------------------------------------------


class TestSensorDescriptions:
    """Verify the sensor entity description tuples are correct."""

    def test_four_sensors_defined(self) -> None:
        assert len(DATA_SENSORS) == 4

    def test_usage_sensor_config(self) -> None:
        desc = DATA_SENSORS[0]
        assert desc.key == ATTR_USAGE
        assert desc.device_class == SensorDeviceClass.DATA_SIZE
        assert desc.native_unit_of_measurement == UnitOfInformation.GIGABYTES
        assert desc.state_class == SensorStateClass.MEASUREMENT

    def test_total_sensor_config(self) -> None:
        desc = DATA_SENSORS[1]
        assert desc.key == ATTR_TOTAL
        assert desc.icon == "mdi:sim-outline"

    def test_remaining_sensor_config(self) -> None:
        desc = DATA_SENSORS[2]
        assert desc.key == ATTR_REMAINING

    def test_percentage_sensor_config(self) -> None:
        desc = DATA_SENSORS[3]
        assert desc.key == ATTR_USAGE_PERCENTAGE
        assert desc.native_unit_of_measurement == PERCENTAGE
        assert desc.icon == "mdi:percent"


# ---------------------------------------------------------------------------
# Sensor value_fn lambdas
# ---------------------------------------------------------------------------


class TestSensorValueFunctions:
    """Test the value_fn callables produce correct values."""

    def test_usage_value(self) -> None:
        assert DATA_SENSORS[0].value_fn(MOCK_COORDINATOR_DATA) == 3.5

    def test_total_value(self) -> None:
        assert DATA_SENSORS[1].value_fn(MOCK_COORDINATOR_DATA) == 10.0

    def test_remaining_value(self) -> None:
        assert DATA_SENSORS[2].value_fn(MOCK_COORDINATOR_DATA) == 6.5

    def test_percentage_value_rounded(self) -> None:
        assert DATA_SENSORS[3].value_fn(MOCK_COORDINATOR_DATA) == 35.0

    def test_percentage_value_none(self) -> None:
        data = {**MOCK_COORDINATOR_DATA, ATTR_USAGE_PERCENTAGE: None}
        assert DATA_SENSORS[3].value_fn(data) is None

    def test_all_values_missing(self) -> None:
        empty: dict[str, Any] = {}
        assert DATA_SENSORS[0].value_fn(empty) is None
        assert DATA_SENSORS[1].value_fn(empty) is None
        assert DATA_SENSORS[2].value_fn(empty) is None
        assert DATA_SENSORS[3].value_fn(empty) is None


# ---------------------------------------------------------------------------
# MozillionSensor entity
# ---------------------------------------------------------------------------


class TestMozillionSensorEntity:
    """Tests for the MozillionSensor entity class."""

    def _make_sensor(
        self,
        data: dict[str, Any] | None = None,
        description: MozillionSensorEntityDescription | None = None,
        sim_number: str = "07700900000",
    ) -> MozillionSensor:
        coordinator = MagicMock()
        coordinator.data = data or MOCK_COORDINATOR_DATA
        entry = _make_config_entry()
        desc = description or DATA_SENSORS[0]  # Usage sensor by default
        return MozillionSensor(coordinator, entry, sim_number, desc)

    def test_unique_id(self) -> None:
        sensor = self._make_sensor()
        assert sensor._attr_unique_id == f"test_entry_id_{ATTR_USAGE}"

    def test_device_info(self) -> None:
        sensor = self._make_sensor()
        assert sensor._attr_device_info["manufacturer"] == "Mozillion"
        assert sensor._attr_device_info["suggested_area"] == "Network"

    def test_device_info_with_sim_number(self) -> None:
        sensor = self._make_sensor(sim_number="07700900000")
        assert (DOMAIN, "07700900000") in sensor._attr_device_info["identifiers"]
        assert sensor._attr_device_info["name"] == "Mozillion 07700900000"

    def test_device_info_without_sim_number(self) -> None:
        sensor = self._make_sensor(sim_number="")
        assert (DOMAIN, "test_entry_id") in sensor._attr_device_info["identifiers"]
        assert sensor._attr_device_info["name"] == "Mozillion"

    def test_native_value_usage(self) -> None:
        sensor = self._make_sensor()
        assert sensor.native_value == 3.5

    def test_native_value_total(self) -> None:
        sensor = self._make_sensor(description=DATA_SENSORS[1])
        assert sensor.native_value == 10.0

    def test_native_value_remaining(self) -> None:
        sensor = self._make_sensor(description=DATA_SENSORS[2])
        assert sensor.native_value == 6.5

    def test_native_value_percentage(self) -> None:
        sensor = self._make_sensor(description=DATA_SENSORS[3])
        assert sensor.native_value == 35.0

    def test_extra_state_attributes(self) -> None:
        sensor = self._make_sensor()
        attrs = sensor.extra_state_attributes
        assert ATTR_RAW in attrs
        assert attrs[ATTR_RAW] == MOCK_COORDINATOR_DATA[ATTR_RAW]

    def test_has_entity_name(self) -> None:
        sensor = self._make_sensor()
        assert sensor._attr_has_entity_name is True


# ---------------------------------------------------------------------------
# MozillionUnlimitedSensor (binary sensor)
# ---------------------------------------------------------------------------


class TestUnlimitedBinarySensor:
    """Tests for the MozillionUnlimitedSensor entity."""

    def _make_unlimited_sensor(
        self,
        data: dict[str, Any] | None = None,
        sim_number: str = "07700900000",
    ) -> MozillionUnlimitedSensor:
        coordinator = MagicMock()
        coordinator.data = data or MOCK_COORDINATOR_DATA
        entry = _make_config_entry()
        return MozillionUnlimitedSensor(coordinator, entry, sim_number)

    def test_unique_id(self) -> None:
        sensor = self._make_unlimited_sensor()
        assert sensor._attr_unique_id == "test_entry_id_unlimited"

    def test_initial_state_is_off(self) -> None:
        sensor = self._make_unlimited_sensor()
        assert sensor._attr_is_on is False

    def test_name(self) -> None:
        sensor = self._make_unlimited_sensor()
        assert sensor._attr_name == "Unlimited"

    def test_icon(self) -> None:
        sensor = self._make_unlimited_sensor()
        assert sensor._attr_icon == "mdi:infinity"

    def test_device_info(self) -> None:
        sensor = self._make_unlimited_sensor()
        assert sensor._attr_device_info["manufacturer"] == "Mozillion"
        assert sensor._attr_device_info["suggested_area"] == "Network"

    def test_handle_coordinator_update_true(self) -> None:
        """When unlimited is True, sensor should be on."""
        sensor = self._make_unlimited_sensor(data=MOCK_COORDINATOR_DATA_UNLIMITED)
        # Mock the parent's write_ha_state
        sensor.async_write_ha_state = MagicMock()
        sensor._handle_coordinator_update()
        assert sensor._attr_is_on is True

    def test_handle_coordinator_update_false(self) -> None:
        """When unlimited is False, sensor should be off."""
        sensor = self._make_unlimited_sensor(data=MOCK_COORDINATOR_DATA)
        sensor.async_write_ha_state = MagicMock()
        sensor._handle_coordinator_update()
        assert sensor._attr_is_on is False

    def test_handle_coordinator_update_missing(self) -> None:
        """When unlimited field is missing, sensor should be off."""
        sensor = self._make_unlimited_sensor(data={})
        sensor.async_write_ha_state = MagicMock()
        sensor._handle_coordinator_update()
        assert sensor._attr_is_on is False

    def test_device_info_without_sim(self) -> None:
        sensor = self._make_unlimited_sensor(sim_number="")
        assert sensor._attr_device_info["name"] == "Mozillion"
        assert (DOMAIN, "test_entry_id") in sensor._attr_device_info["identifiers"]
