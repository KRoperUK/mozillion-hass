"""Tests for the Mozillion diagnostics download.

The entry is the account and each SIM is a subentry, so the redaction has to cover
both -- plus the coordinator payload, which repeats the SIM's identifiers.
"""

from __future__ import annotations

from typing import Any
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
)
from custom_components.mozillion.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_ENTRY_DATA_LOGIN,
    _make_config_entry,
    sim_subentry,
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

# The SIM's identifiers sit on the subentry now, and are redacted too.
IDENTIFIERS = {
    CONF_SIM_NUMBER: "07700900000",
    CONF_ICCID: "89440000000000000000",
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


def _entry_with_coordinator(hass: HomeAssistant) -> tuple[MockConfigEntry, str]:
    """Return an account entry with one SIM coordinator attached."""

    entry = _make_config_entry(data={**MOCK_ENTRY_DATA_LOGIN, **SECRETS})
    entry.add_to_hass(hass)
    subentry_id = sim_subentry(entry).subentry_id

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
        client=MagicMock(),
        session=MagicMock(),
        coordinators={subentry_id: coordinator},
    )
    return entry, subentry_id


async def test_secrets_are_redacted(hass: HomeAssistant) -> None:
    """Nothing that grants access may survive into a diagnostics download."""
    entry, subentry_id = _entry_with_coordinator(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    dumped = str(diagnostics)
    for key, value in SECRETS.items():
        assert value not in dumped, f"{key} leaked into diagnostics"

    # Redaction reaches the subentry and the coordinator payload, not just entry.data.
    subentry_data = diagnostics["subentries"][subentry_id]["data"]
    assert subentry_data[CONF_SIM_NUMBER] == "**REDACTED**"
    assert subentry_data[CONF_ICCID] == "**REDACTED**"
    assert diagnostics["coordinators"][subentry_id]["data"]["sim_number"] == (
        "**REDACTED**"
    )
    assert diagnostics["coordinators"][subentry_id]["data"]["iccid"] == "**REDACTED**"


async def test_usage_figures_survive(hass: HomeAssistant) -> None:
    """The values diagnostics exist to explain must still be present."""
    entry, subentry_id = _entry_with_coordinator(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    payload: dict[str, Any] = diagnostics["coordinators"][subentry_id]["data"]
    assert payload["usage"] == 3.5
    assert payload["total"] == 10.0
    assert payload["raw"] == MOCK_API_RESPONSE


async def test_coordinator_health_is_reported(hass: HomeAssistant) -> None:
    entry, subentry_id = _entry_with_coordinator(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    health = diagnostics["coordinators"][subentry_id]
    assert health["last_update_success"] is True
    assert health["last_exception"] is None
    assert diagnostics["entry"]["version"] == 3
    assert diagnostics["subentries"][subentry_id]["type"] == "sim"


async def test_exception_is_reported_as_a_string(hass: HomeAssistant) -> None:
    """A raw exception object is not serialisable."""
    entry, subentry_id = _entry_with_coordinator(hass)
    entry.runtime_data.coordinators[subentry_id].last_exception = RuntimeError("boom")

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["coordinators"][subentry_id]["last_exception"] == "boom"
