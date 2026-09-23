"""Tests for Mozillion entry setup, teardown and the resulting entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.mozillion.api import MozillionAuthError
from custom_components.mozillion.const import (
    ATTR_BILLING_AMOUNT,
    ATTR_HAS_BILL,
    ATTR_PLAN_PARENTAL_CONTROL,
    ATTR_PLAN_VOICEMAIL,
    ATTR_PORT_DATE,
    ATTR_PORT_STATUS,
    ATTR_USAGE,
    DOMAIN,
)
from custom_components.mozillion.coordinator import MozillionCoordinator
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_ENTRY_DATA_COOKIE,
    MOCK_SIM,
    MOCK_WALLET,
    _make_config_entry,
    sim_subentry,
)

pytestmark = pytest.mark.asyncio

CLIENT = "custom_components.mozillion.MozillionClient"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


def _entry(hass: HomeAssistant, **overrides) -> MockConfigEntry:
    """An account entry with one SIM subentry, as the config flow creates it."""

    entry = _make_config_entry(data={**MOCK_ENTRY_DATA_COOKIE, **overrides})
    entry.add_to_hass(hass)
    return entry


def _client(**overrides) -> MagicMock:
    client = MagicMock()
    client.async_get_usage = AsyncMock(return_value=MOCK_API_RESPONSE)
    client.async_login = AsyncMock(
        return_value=("mozillion_session=abc; XSRF-TOKEN=xyz", "xyz")
    )
    client.async_fetch_sims = AsyncMock(return_value=[])
    client.async_fetch_sim = AsyncMock(return_value=MOCK_SIM)
    client.async_fetch_overspend = AsyncMock(return_value=MOCK_WALLET)
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def _sim_coordinator(entry) -> MozillionCoordinator:
    """Return the coordinator polling the entry's first SIM."""

    return entry.runtime_data.coordinators[sim_subentry(entry).subentry_id]


def _sensors(hass: HomeAssistant) -> dict[str, str]:
    """Return the sensor states, keyed by the entity's name-ish suffix.

    The device carries a suggested area ("Network"), which Home Assistant bakes
    into the generated entity id, so the ids are not asserted literally.
    """
    return {
        state.entity_id: state.state
        for state in hass.states.async_all()
        if state.domain == "sensor"
    }


def _sensor_ending(hass: HomeAssistant, suffix: str) -> State:
    for state in hass.states.async_all():
        if state.domain == "sensor" and state.entity_id.endswith(suffix):
            return state
    raise AssertionError(f"no sensor ending in {suffix!r}")


async def test_setup_creates_the_expected_entities(hass: HomeAssistant) -> None:
    entry = _entry(hass)

    with patch(CLIENT, return_value=_client()):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    coordinator = _sim_coordinator(entry)
    assert coordinator.data[ATTR_USAGE] == 3.5

    assert _sensor_ending(hass, "_usage").state == "3.5"
    assert _sensor_ending(hass, "_total").state == "10.0"
    assert _sensor_ending(hass, "_remaining").state == "6.5"
    assert _sensor_ending(hass, "_usage_percentage").state == "35.0"
    # The dashboard-derived extras.
    assert _sensor_ending(hass, "_data_resets").state == "2026-10-19"
    assert _sensor_ending(hass, "_wallet_balance").state == "9.5"
    assert _sensor_ending(hass, "_out_of_bundle_spend").state == "2.5"
    assert _sensor_ending(hass, "_sim_status").state == "ACTIVE"

    binary = [
        state for state in hass.states.async_all() if state.domain == "binary_sensor"
    ]
    assert sorted(state.state for state in binary) == ["off", "off"]


async def test_device_carries_the_plan_as_its_model(hass: HomeAssistant) -> None:
    entry = _entry(hass)

    with patch(CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(devices) == 1
    assert devices[0].model == "10GB"
    assert devices[0].serial_number == "89440000000000000000"
    assert (DOMAIN, "7654321") in devices[0].identifiers


async def test_entities_carry_the_raw_payload(hass: HomeAssistant) -> None:
    entry = _entry(hass)

    with patch(CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = _sensor_ending(hass, "_usage")
    assert state.attributes["raw"] == MOCK_API_RESPONSE
    assert state.attributes["usage_gbr"] == 3.5
    assert state.attributes["total_global"] == 0.0


async def test_unload_removes_the_entities(hass: HomeAssistant) -> None:
    entry = _entry(hass)

    with patch(CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id) is True
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    # Unloaded entities stop reporting values.
    assert not [v for v in _sensors(hass).values() if v != "unavailable"]


async def test_failed_first_refresh_defers_setup(hass: HomeAssistant) -> None:
    """A transient API failure must not mark the entry as broken."""
    entry = _entry(hass)
    client = _client(async_get_usage=AsyncMock(side_effect=RuntimeError("site down")))

    with patch(CLIENT, return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_bad_credentials_trigger_reauth(hass: HomeAssistant) -> None:
    """Rejected credentials must ask the user, not retry forever."""
    entry = _entry(hass, email="user@example.com", password="wrong")
    client = _client(async_login=AsyncMock(side_effect=MozillionAuthError("nope")))

    with patch(CLIENT, return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_scan_interval_drives_the_schedule(hass: HomeAssistant) -> None:
    """The stored scan interval drives the coordinator's schedule."""
    entry = _make_config_entry(
        data=dict(MOCK_ENTRY_DATA_COOKIE), options={"scan_interval": 7200}
    )
    entry.add_to_hass(hass)

    with patch(CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator: MozillionCoordinator = _sim_coordinator(entry)
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 7200


async def test_coordinator_is_bound_to_the_entry(hass: HomeAssistant) -> None:
    """Without this, async_config_entry_first_refresh refuses to run."""
    entry = _entry(hass)

    with patch(CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _sim_coordinator(entry).config_entry is entry


async def test_setup_uses_the_configured_credentials(hass: HomeAssistant) -> None:
    """A login entry must authenticate before its first poll."""
    entry = _entry(hass, email="user@example.com", password="secret123")
    client = _client()

    with patch(CLIENT, return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Exactly one login: setup must not authenticate and then let the
    # coordinator's first refresh authenticate again.
    client.async_login.assert_awaited_once()
    assert entry.state is ConfigEntryState.LOADED


async def test_an_account_with_no_sims_is_a_setup_error(
    hass: HomeAssistant,
) -> None:
    """An account entry with no SIM has nothing to poll, and says so.

    Raising ConfigEntryError rather than returning False puts the entry in
    SETUP_ERROR, which surfaces the reason instead of a silent failure.
    """

    entry = _make_config_entry(subentries=[])
    entry.add_to_hass(hass)

    with patch(CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_the_port_status_and_plan_details_are_exposed(
    hass: HomeAssistant,
) -> None:
    """The dashboard's porting and plan detail reach Home Assistant.

    The port label is the sensor's state and the machine value stays in the
    attributes, because Mozillion uses status values this integration has not
    seen and inventing an enum would be guesswork.
    """

    entry = _entry(hass)

    with patch(CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    port = _sensor_ending(hass, "_number_port_status")
    assert port.state == "Done"

    status = _sensor_ending(hass, "_sim_status")
    assert status.attributes[ATTR_PORT_STATUS] == "DONE"
    assert status.attributes[ATTR_PORT_DATE] == "29-12-2025"
    assert status.attributes[ATTR_PLAN_VOICEMAIL] is True
    assert status.attributes[ATTR_PLAN_PARENTAL_CONTROL] is False
    assert status.attributes[ATTR_BILLING_AMOUNT] == 999.0
    assert status.attributes[ATTR_HAS_BILL] is False
