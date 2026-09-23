"""Tests for the Mozillion sensor and binary sensor platforms."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from custom_components.mozillion.binary_sensor import (
    MozillionOverspendSensor,
    MozillionUnlimitedSensor,
)
from custom_components.mozillion.const import (
    ATTR_ICCID,
    ATTR_OVERSPEND_LIMIT_REACHED,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_RESET_DATE,
    ATTR_SIM_STATUS,
    ATTR_TOTAL,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    ATTR_WALLET,
    ATTR_WALLET_BALANCE,
    ATTR_WALLET_SPEND,
    DOMAIN,
)
from custom_components.mozillion.sensor import (
    DATA_SENSORS,
    MozillionSensor,
    MozillionSensorEntityDescription,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.helpers.entity import EntityCategory

from tests.conftest import (
    MOCK_COORDINATOR_DATA,
    MOCK_COORDINATOR_DATA_NO_WALLET,
    MOCK_COORDINATOR_DATA_UNLIMITED,
    _make_config_entry,
    _sim_subentry_data,
    sim_subentry,
)

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "mozillion"
)


def _sensor(
    data: dict[str, Any] | None = None,
    description: MozillionSensorEntityDescription | None = None,
    subentries: list[dict[str, Any]] | None = None,
) -> MozillionSensor:
    """Build a sensor for the entry's first SIM, backed by a mock coordinator."""

    coordinator = MagicMock()
    coordinator.data = data if data is not None else MOCK_COORDINATOR_DATA
    entry = _make_config_entry(subentries=subentries)
    return MozillionSensor(
        coordinator, entry, sim_subentry(entry), description or DATA_SENSORS[0]
    )


def _sensor_for(key: str, data: dict[str, Any] | None = None) -> MozillionSensor:
    """Build the sensor with this attribute key."""

    description = next(desc for desc in DATA_SENSORS if desc.key == key)
    return _sensor(data=data, description=description)


def _binary_sensor(cls, data: dict[str, Any] | None = None):
    """Build a binary sensor for the entry's first SIM."""

    coordinator = MagicMock()
    coordinator.data = data if data is not None else MOCK_COORDINATOR_DATA
    entry = _make_config_entry()
    return cls(coordinator, entry, sim_subentry(entry))


def _unlimited_sensor(
    data: dict[str, Any] | None = None,
) -> MozillionUnlimitedSensor:
    """Build an unlimited binary sensor backed by a mock coordinator."""

    return _binary_sensor(MozillionUnlimitedSensor, data)


def _overspend_sensor(
    data: dict[str, Any] | None = None,
) -> MozillionOverspendSensor:
    """Build an overspend binary sensor backed by a mock coordinator."""

    return _binary_sensor(MozillionOverspendSensor, data)


# ---------------------------------------------------------------------------
# Sensor entity descriptions
# ---------------------------------------------------------------------------


class TestSensorDescriptions:
    """Verify the sensor entity description tuples are correct."""

    def test_sensor_keys(self) -> None:
        assert [desc.key for desc in DATA_SENSORS] == [
            ATTR_USAGE,
            ATTR_TOTAL,
            ATTR_REMAINING,
            ATTR_USAGE_PERCENTAGE,
            ATTR_RESET_DATE,
            ATTR_WALLET_BALANCE,
            ATTR_WALLET_SPEND,
            ATTR_SIM_STATUS,
        ]

    def test_usage_sensor_config(self) -> None:
        desc = next(desc for desc in DATA_SENSORS if desc.key == ATTR_USAGE)
        assert desc.translation_key == "usage"
        assert desc.device_class == SensorDeviceClass.DATA_SIZE
        assert desc.native_unit_of_measurement == UnitOfInformation.GIGABYTES
        assert desc.state_class == SensorStateClass.MEASUREMENT

    def test_percentage_sensor_config(self) -> None:
        desc = next(desc for desc in DATA_SENSORS if desc.key == ATTR_USAGE_PERCENTAGE)
        assert desc.native_unit_of_measurement == PERCENTAGE

    def test_reset_date_sensor_config(self) -> None:
        desc = next(desc for desc in DATA_SENSORS if desc.key == ATTR_RESET_DATE)
        assert desc.device_class == SensorDeviceClass.DATE

    def test_wallet_sensors_are_monetary(self) -> None:
        """Monetary readings only allow the TOTAL state class in HA."""
        for key in (ATTR_WALLET_BALANCE, ATTR_WALLET_SPEND):
            desc = next(desc for desc in DATA_SENSORS if desc.key == key)
            assert desc.device_class == SensorDeviceClass.MONETARY
            assert desc.native_unit_of_measurement == "GBP"
            assert desc.state_class == SensorStateClass.TOTAL

    def test_sim_status_is_diagnostic(self) -> None:
        desc = next(desc for desc in DATA_SENSORS if desc.key == ATTR_SIM_STATUS)
        assert desc.entity_category == EntityCategory.DIAGNOSTIC

    def test_only_the_wallet_sensors_are_conditional(self) -> None:
        conditional = {d.key for d in DATA_SENSORS if d.available_fn is not None}
        assert conditional == {ATTR_WALLET_BALANCE, ATTR_WALLET_SPEND}

    def test_keys_are_unique(self) -> None:
        keys = [desc.key for desc in DATA_SENSORS]
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Sensor value_fn lambdas
# ---------------------------------------------------------------------------


class TestSensorValueFunctions:
    """Test the value_fn callables produce correct values."""

    def test_usage_value(self) -> None:
        desc = next(d for d in DATA_SENSORS if d.key == ATTR_USAGE)
        assert desc.value_fn(MOCK_COORDINATOR_DATA) == 3.5

    def test_remaining_value(self) -> None:
        desc = next(d for d in DATA_SENSORS if d.key == ATTR_REMAINING)
        assert desc.value_fn(MOCK_COORDINATOR_DATA) == 6.5

    def test_percentage_value_rounded(self) -> None:
        desc = next(d for d in DATA_SENSORS if d.key == ATTR_USAGE_PERCENTAGE)
        assert desc.value_fn(MOCK_COORDINATOR_DATA) == 35.0

    def test_all_values_missing(self) -> None:
        empty: dict[str, Any] = {}
        for key in (
            ATTR_USAGE,
            ATTR_TOTAL,
            ATTR_REMAINING,
            ATTR_USAGE_PERCENTAGE,
            ATTR_WALLET_BALANCE,
            ATTR_WALLET_SPEND,
        ):
            desc = next(d for d in DATA_SENSORS if d.key == key)
            assert desc.value_fn(empty) is None


# ---------------------------------------------------------------------------
# MozillionSensor entity
# ---------------------------------------------------------------------------


class TestMozillionSensorEntity:
    """Tests for the MozillionSensor entity class."""

    def test_unique_id(self) -> None:
        """Scoped by SIM, so a second SIM on the account cannot collide."""
        sensor = _sensor()
        subentry = sensor._subentry
        assert sensor._attr_unique_id == (
            f"test_entry_id_{subentry.subentry_id}_{ATTR_USAGE}"
        )

    def test_has_entity_name(self) -> None:
        assert _sensor()._attr_has_entity_name is True

    def test_device_info(self) -> None:
        info = _sensor().device_info
        assert info["manufacturer"] == "Mozillion"
        assert info["suggested_area"] == "Network"

    def test_device_identifier_is_the_sim_meta_id(self) -> None:
        """Identity must not depend on the phone number, which can change."""
        assert (DOMAIN, "7654321") in _sensor().device_info["identifiers"]

    def test_device_info_without_sim_meta_id_falls_back_to_subentry_id(self) -> None:
        sensor = _sensor(subentries=[_sim_subentry_data(sim_meta_id="", sim_number="")])
        info = sensor.device_info
        assert (DOMAIN, sensor._subentry.subentry_id) in info["identifiers"]
        assert info["name"] == "Mozillion"

    def test_device_info_names_the_sim_number(self) -> None:
        assert _sensor().device_info["name"] == "Mozillion 07700900000"

    def test_device_info_serial_is_the_iccid(self) -> None:
        assert _sensor().device_info["serial_number"] == "89440000000000000000"

    def test_device_model_is_the_plan_tariff(self) -> None:
        assert _sensor().device_info["model"] == "10GB"

    def test_device_model_follows_a_plan_change(self) -> None:
        data = {**MOCK_COORDINATOR_DATA, "plan_tariff": "250GB"}
        assert _sensor(data=data).device_info["model"] == "250GB"

    def test_device_info_survives_a_missing_plan(self) -> None:
        """The plan is an extra: the device still exists without it."""
        data = {**MOCK_COORDINATOR_DATA, "plan_tariff": ""}
        assert "model" not in _sensor(data=data).device_info

    def test_native_values(self) -> None:
        assert _sensor_for(ATTR_USAGE).native_value == 3.5
        assert _sensor_for(ATTR_TOTAL).native_value == 10.0
        assert _sensor_for(ATTR_REMAINING).native_value == 6.5
        assert _sensor_for(ATTR_USAGE_PERCENTAGE).native_value == 35.0

    def test_reset_date_value(self) -> None:
        assert _sensor_for(ATTR_RESET_DATE).native_value == date(2026, 10, 19)

    def test_wallet_values(self) -> None:
        assert _sensor_for(ATTR_WALLET_BALANCE).native_value == 9.5
        assert _sensor_for(ATTR_WALLET_SPEND).native_value == 2.5

    def test_sim_status_value(self) -> None:
        assert _sensor_for(ATTR_SIM_STATUS).native_value == "ACTIVE"

    def test_sim_status_is_unknown_when_blank(self) -> None:
        data = {**MOCK_COORDINATOR_DATA, ATTR_SIM_STATUS: ""}
        assert _sensor_for(ATTR_SIM_STATUS, data=data).native_value is None

    def test_native_value_unlimited_is_unknown(self) -> None:
        """An unlimited plan has no allowance to compute a balance from."""
        sensor = _sensor_for(ATTR_REMAINING, data=MOCK_COORDINATOR_DATA_UNLIMITED)
        assert sensor.native_value is None

    def test_extra_state_attributes(self) -> None:
        attrs = _sensor().extra_state_attributes
        assert attrs[ATTR_RAW] == MOCK_COORDINATOR_DATA[ATTR_RAW]
        assert attrs[ATTR_ICCID] == "89440000000000000000"
        assert attrs["usage_gbr"] == 3.5
        assert attrs["total_global"] == 0.0
        assert attrs["reset_label"] == "19 Oct"
        assert attrs["plan_tariff"] == "10GB"
        assert attrs[ATTR_WALLET] == MOCK_COORDINATOR_DATA[ATTR_WALLET]


# ---------------------------------------------------------------------------
# Wallet availability
# ---------------------------------------------------------------------------


class TestWalletAvailability:
    """The wallet entities report unavailable when there is no wallet."""

    def test_available_when_the_wallet_is_in_use(self) -> None:
        assert _sensor_for(ATTR_WALLET_BALANCE).available is True
        assert _sensor_for(ATTR_WALLET_SPEND).available is True
        assert _overspend_sensor().available is True

    def test_unavailable_for_a_never_topped_up_wallet(self) -> None:
        """All zeros with reached=true must not read as "you hit your limit"."""
        for key in (ATTR_WALLET_BALANCE, ATTR_WALLET_SPEND):
            sensor = _sensor_for(key, data=MOCK_COORDINATOR_DATA_NO_WALLET)
            assert sensor.available is False
        assert _overspend_sensor(MOCK_COORDINATOR_DATA_NO_WALLET).available is False

    def test_unavailable_when_the_wallet_could_not_be_read(self) -> None:
        data = {**MOCK_COORDINATOR_DATA, ATTR_WALLET: None, ATTR_WALLET_BALANCE: None}
        assert _sensor_for(ATTR_WALLET_BALANCE, data=data).available is False

    def test_data_sensors_are_always_available(self) -> None:
        assert _sensor_for(ATTR_USAGE).available is True
        assert _sensor_for(ATTR_RESET_DATE).available is True


# ---------------------------------------------------------------------------
# Binary sensors
# ---------------------------------------------------------------------------


class TestUnlimitedBinarySensor:
    """Tests for the MozillionUnlimitedSensor entity."""

    def test_unique_id(self) -> None:
        sensor = _unlimited_sensor()
        assert sensor._attr_unique_id == (
            f"test_entry_id_{sensor._subentry.subentry_id}_unlimited"
        )

    def test_translation_key(self) -> None:
        assert _unlimited_sensor()._attr_translation_key == "unlimited"

    def test_device_info(self) -> None:
        info = _unlimited_sensor().device_info
        assert info["manufacturer"] == "Mozillion"
        assert (DOMAIN, "7654321") in info["identifiers"]

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


class TestOverspendBinarySensor:
    """Tests for the MozillionOverspendSensor entity."""

    def test_unique_id(self) -> None:
        sensor = _overspend_sensor()
        assert sensor._attr_unique_id == (
            f"test_entry_id_{sensor._subentry.subentry_id}_"
            f"{ATTR_OVERSPEND_LIMIT_REACHED}"
        )

    def test_translation_key(self) -> None:
        assert _overspend_sensor()._attr_translation_key == "overspend_limit_reached"

    def test_is_off_when_the_limit_is_not_reached(self) -> None:
        assert _overspend_sensor().is_on is False

    def test_is_on_when_the_site_reports_it(self) -> None:
        data = {**MOCK_COORDINATOR_DATA, ATTR_OVERSPEND_LIMIT_REACHED: True}
        assert _overspend_sensor(data=data).is_on is True

    def test_reports_the_raw_wallet_payload(self) -> None:
        attrs = _overspend_sensor().extra_state_attributes
        assert attrs[ATTR_WALLET] == MOCK_COORDINATOR_DATA[ATTR_WALLET]


# ---------------------------------------------------------------------------
# Icons / translations coverage
# ---------------------------------------------------------------------------


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))


class TestIconTranslations:
    """Every translated entity must carry an icon."""

    def test_sensors_have_icons(self) -> None:
        icons = _load_json("icons.json")["entity"]["sensor"]
        for desc in DATA_SENSORS:
            assert desc.translation_key in icons, f"no icon for {desc.key}"
            assert icons[desc.translation_key]["default"].startswith("mdi:")

    def test_binary_sensors_have_icons(self) -> None:
        icons = _load_json("icons.json")["entity"]["binary_sensor"]
        assert icons["unlimited"]["default"] == "mdi:infinity"
        assert icons["overspend_limit_reached"]["default"].startswith("mdi:")


class TestEntityTranslations:
    """Entity names live in the translation files."""

    def test_sensors_have_names(self) -> None:
        entities = _load_json("strings.json")["entity"]["sensor"]
        for desc in DATA_SENSORS:
            assert entities[desc.translation_key]["name"]

    def test_binary_sensors_have_names(self) -> None:
        entities = _load_json("strings.json")["entity"]["binary_sensor"]
        assert entities["unlimited"]["name"] == "Unlimited"
        assert entities["overspend_limit_reached"]["name"]
