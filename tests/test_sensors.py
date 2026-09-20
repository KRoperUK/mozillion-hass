"""Tests for the Mozillion sensor and binary sensor platforms."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from custom_components.mozillion.binary_sensor import MozillionUnlimitedSensor
from custom_components.mozillion.const import (
    ATTR_ICCID,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_TOTAL,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    DOMAIN,
)
from custom_components.mozillion.sensor import (
    DATA_SENSORS,
    MozillionSensor,
    MozillionSensorEntityDescription,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfInformation

from tests.conftest import (
    MOCK_COORDINATOR_DATA,
    MOCK_COORDINATOR_DATA_UNLIMITED,
    _make_config_entry,
)

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "mozillion"
)


def _sensor(
    data: dict[str, Any] | None = None,
    description: MozillionSensorEntityDescription | None = None,
    data_overrides: dict[str, Any] | None = None,
) -> MozillionSensor:
    """Build a sensor backed by a mock coordinator."""

    coordinator = MagicMock()
    coordinator.data = data or MOCK_COORDINATOR_DATA
    entry = _make_config_entry(data=data_overrides)
    return MozillionSensor(coordinator, entry, description or DATA_SENSORS[0])


def _unlimited_sensor(
    data: dict[str, Any] | None = None,
) -> MozillionUnlimitedSensor:
    """Build an unlimited binary sensor backed by a mock coordinator."""

    coordinator = MagicMock()
    coordinator.data = data or MOCK_COORDINATOR_DATA
    return MozillionUnlimitedSensor(coordinator, _make_config_entry())


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
        assert desc.translation_key == "usage"
        assert desc.device_class == SensorDeviceClass.DATA_SIZE
        assert desc.native_unit_of_measurement == UnitOfInformation.GIGABYTES
        assert desc.state_class == SensorStateClass.MEASUREMENT

    def test_total_sensor_config(self) -> None:
        assert DATA_SENSORS[1].key == ATTR_TOTAL
        assert DATA_SENSORS[1].translation_key == "total"

    def test_remaining_sensor_config(self) -> None:
        assert DATA_SENSORS[2].key == ATTR_REMAINING
        assert DATA_SENSORS[2].translation_key == "remaining"

    def test_percentage_sensor_config(self) -> None:
        desc = DATA_SENSORS[3]
        assert desc.key == ATTR_USAGE_PERCENTAGE
        assert desc.native_unit_of_measurement == PERCENTAGE
        assert desc.translation_key == "usage_percentage"

    def test_keys_are_unique(self) -> None:
        keys = [desc.key for desc in DATA_SENSORS]
        assert len(keys) == len(set(keys))


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

    def test_unique_id(self) -> None:
        assert _sensor()._attr_unique_id == f"test_entry_id_{ATTR_USAGE}"

    def test_has_entity_name(self) -> None:
        assert _sensor()._attr_has_entity_name is True

    def test_device_info(self) -> None:
        info = _sensor()._attr_device_info
        assert info["manufacturer"] == "Mozillion"
        assert info["suggested_area"] == "Network"

    def test_device_identifier_is_the_sim_meta_id(self) -> None:
        """Identity must not depend on the phone number, which can change."""
        info = _sensor()._attr_device_info
        assert (DOMAIN, "21919") in info["identifiers"]

    def test_device_info_without_sim_meta_id_falls_back_to_entry_id(self) -> None:
        sensor = _sensor(data_overrides={"sim_meta_id": "", "sim_number": ""})
        info = sensor._attr_device_info
        assert (DOMAIN, "test_entry_id") in info["identifiers"]
        assert info["name"] == "Mozillion"

    def test_device_info_names_the_sim_number(self) -> None:
        assert _sensor()._attr_device_info["name"] == "Mozillion 07700900000"

    def test_device_info_serial_is_the_iccid(self) -> None:
        assert _sensor()._attr_device_info["serial_number"] == ("89443042334117134260")

    def test_native_value_usage(self) -> None:
        assert _sensor().native_value == 3.5

    def test_native_value_total(self) -> None:
        assert _sensor(description=DATA_SENSORS[1]).native_value == 10.0

    def test_native_value_remaining(self) -> None:
        assert _sensor(description=DATA_SENSORS[2]).native_value == 6.5

    def test_native_value_percentage(self) -> None:
        assert _sensor(description=DATA_SENSORS[3]).native_value == 35.0

    def test_native_value_unlimited_is_unknown(self) -> None:
        """An unlimited plan has no allowance to compute a balance from."""
        assert (
            _sensor(
                data=MOCK_COORDINATOR_DATA_UNLIMITED, description=DATA_SENSORS[2]
            ).native_value
            is None
        )

    def test_extra_state_attributes(self) -> None:
        attrs = _sensor().extra_state_attributes
        assert attrs[ATTR_RAW] == MOCK_COORDINATOR_DATA[ATTR_RAW]
        assert attrs[ATTR_ICCID] == "89443042334117134260"
        assert attrs["usage_gbr"] == 3.5
        assert attrs["total_gbr"] == 10.0
        assert attrs["usage_global"] == 0.0
        assert attrs["total_global"] == 0.0


# ---------------------------------------------------------------------------
# MozillionUnlimitedSensor (binary sensor)
# ---------------------------------------------------------------------------


class TestUnlimitedBinarySensor:
    """Tests for the MozillionUnlimitedSensor entity."""

    def test_unique_id(self) -> None:
        assert _unlimited_sensor()._attr_unique_id == "test_entry_id_unlimited"

    def test_translation_key(self) -> None:
        assert _unlimited_sensor()._attr_translation_key == "unlimited"

    def test_device_info(self) -> None:
        info = _unlimited_sensor()._attr_device_info
        assert info["manufacturer"] == "Mozillion"
        assert (DOMAIN, "21919") in info["identifiers"]

    def test_is_on_false_for_capped_plan(self) -> None:
        assert _unlimited_sensor().is_on is False

    def test_is_on_true_for_unlimited_plan(self) -> None:
        assert _unlimited_sensor(data=MOCK_COORDINATOR_DATA_UNLIMITED).is_on is True

    def test_is_on_false_when_flag_missing(self) -> None:
        assert _unlimited_sensor(data={}).is_on is False

    def test_extra_state_attributes(self) -> None:
        attrs = _unlimited_sensor().extra_state_attributes
        assert attrs[ATTR_USAGE] == 3.5
        assert attrs[ATTR_USAGE_PERCENTAGE] == 35.0


# ---------------------------------------------------------------------------
# Icons / translations coverage
# ---------------------------------------------------------------------------


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((COMPONENT_DIR / name).read_text())


class TestIconTranslations:
    """Every translated entity must carry an icon."""

    def test_sensors_have_icons(self) -> None:
        icons = _load_json("icons.json")["entity"]["sensor"]
        for desc in DATA_SENSORS:
            assert desc.translation_key in icons, f"no icon for {desc.key}"
            assert icons[desc.translation_key]["default"].startswith("mdi:")

    def test_binary_sensor_has_icon(self) -> None:
        icons = _load_json("icons.json")["entity"]["binary_sensor"]
        assert icons["unlimited"]["default"] == "mdi:infinity"


class TestEntityTranslations:
    """Entity names live in the translation files."""

    def test_sensors_have_names(self) -> None:
        entities = _load_json("strings.json")["entity"]["sensor"]
        for desc in DATA_SENSORS:
            assert entities[desc.translation_key]["name"]

    def test_binary_sensor_has_name(self) -> None:
        entities = _load_json("strings.json")["entity"]["binary_sensor"]
        assert entities["unlimited"]["name"] == "Unlimited"

    def test_translations_match_strings(self) -> None:
        assert _load_json("strings.json") == _load_json("translations/en.json")
