"""Tests for the Mozillion diagnostics download."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.mozillion import MozillionRuntimeData
from custom_components.mozillion.const import (
    CONF_EMAIL,
    CONF_ICCID,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DOMAIN,
)
from custom_components.mozillion.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_ENTRY_DATA_LOGIN,
)

pytestmark = pytest.mark.asyncio

# Credentials and session material: these must never survive redaction.
SECRETS = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "hunter2",
    CONF_TOTP_SECRET: "JBSWY3DPEHPK3PXP",
    CONF_SESSION_COOKIE: "mozillion_session=supersecret",
    CONF_XSRF_TOKEN: "xsrf-secret",
}

# Identifiers are redacted in the payloads but still name the device, so they
# appear in the entry title that the UI already shows.
IDENTIFIERS = {
    CONF_SIM_NUMBER: "07700900000",
    CONF_ICCID: "89443042334117134260",
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


def _entry_with_coordinator(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion 07700900000",
        data={**MOCK_ENTRY_DATA_LOGIN, **SECRETS, **IDENTIFIERS},
        options={},
        unique_id="21919",
        version=2,
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.last_exception = None
    coordinator.update_interval = None
    coordinator.data = {
        "raw": MOCK_API_RESPONSE,
        "usage": 3.5,
        "total": 10.0,
        "sim_number": IDENTIFIERS[CONF_SIM_NUMBER],
        "iccid": IDENTIFIERS[CONF_ICCID],
    }
    entry.runtime_data = MozillionRuntimeData(
        client=MagicMock(), coordinator=coordinator
    )
    return entry


async def test_secrets_are_redacted(hass: HomeAssistant) -> None:
    """Nothing that grants access may survive into a diagnostics download."""
    diagnostics = await async_get_config_entry_diagnostics(
        hass, _entry_with_coordinator(hass)
    )

    dumped = str(diagnostics)
    for key, value in SECRETS.items():
        assert value not in dumped, f"{key} leaked into diagnostics"

    # Redaction must reach the nested coordinator payload too, not just entry.data.
    assert diagnostics["entry"]["data"][CONF_SIM_NUMBER] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_ICCID] == "**REDACTED**"
    assert diagnostics["coordinator"]["data"]["sim_number"] == "**REDACTED**"
    assert diagnostics["coordinator"]["data"]["iccid"] == "**REDACTED**"


async def test_usage_figures_survive(hass: HomeAssistant) -> None:
    """The values diagnostics exist to explain must still be present."""
    diagnostics = await async_get_config_entry_diagnostics(
        hass, _entry_with_coordinator(hass)
    )

    assert diagnostics["coordinator"]["data"]["usage"] == 3.5
    assert diagnostics["coordinator"]["data"]["total"] == 10.0
    assert diagnostics["coordinator"]["data"]["raw"] == MOCK_API_RESPONSE


async def test_coordinator_health_is_reported(hass: HomeAssistant) -> None:
    entry = _entry_with_coordinator(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["last_exception"] is None
    assert diagnostics["entry"]["title"] == "Mozillion 07700900000"
    assert diagnostics["entry"]["version"] == 2


async def test_exception_is_reported_as_a_string(hass: HomeAssistant) -> None:
    """A raw exception object is not serialisable."""
    entry = _entry_with_coordinator(hass)
    entry.runtime_data.coordinator.last_exception = RuntimeError("boom")

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["coordinator"]["last_exception"] == "boom"
