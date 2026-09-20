"""Tests for the Mozillion config flow, using the real Home Assistant harness."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.mozillion.api import MozillionAuthError, MozillionSim
from custom_components.mozillion.const import (
    CONF_EMAIL,
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import MOCK_ENTRY_DATA_COOKIE, MOCK_SIM

pytestmark = pytest.mark.asyncio

CLIENT = "custom_components.mozillion.config_flow.MozillionClient"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Register this repo's custom integration with the test hass."""
    yield


@pytest.fixture(autouse=True)
def _bypass_setup():
    """Stop entry setup from reaching the network during flow tests."""

    with patch("custom_components.mozillion.async_setup_entry", return_value=True):
        yield


def _login_input(**overrides: Any) -> dict[str, Any]:
    base = {
        CONF_EMAIL: "user@example.com",
        CONF_PASSWORD: "secret123",
        CONF_TOTP_SECRET: "",
        CONF_SESSION_COOKIE: "",
        CONF_XSRF_TOKEN: "",
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    }
    base.update(overrides)
    return base


def _cookie_input(**overrides: Any) -> dict[str, Any]:
    base = {
        CONF_EMAIL: "",
        CONF_PASSWORD: "",
        CONF_TOTP_SECRET: "",
        CONF_SESSION_COOKIE: "mozillion_session=abc; XSRF-TOKEN=xyz",
        CONF_XSRF_TOKEN: "xyz",
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    }
    base.update(overrides)
    return base


def _credentials_only(data: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that are not part of the reauth credentials form."""
    return {key: value for key, value in data.items() if key != CONF_SCAN_INTERVAL}


def _mock_client(
    *,
    sims: list[Any] | None = None,
    login_error: Exception | None = None,
    usage_error: Exception | None = None,
) -> MagicMock:
    """Build a stand-in MozillionClient."""

    client = MagicMock()
    client.async_login = AsyncMock(
        side_effect=login_error,
        return_value=("mozillion_session=abc; XSRF-TOKEN=xyz", "xyz"),
    )
    client.async_fetch_sims = AsyncMock(
        side_effect=usage_error, return_value=sims if sims is not None else [MOCK_SIM]
    )
    client.async_get_usage = AsyncMock(
        side_effect=usage_error, return_value={"status": "success"}
    )
    return client


async def _start_user_flow(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


# ---------------------------------------------------------------------------
# async_step_user
# ---------------------------------------------------------------------------


async def test_user_form_is_shown_first(hass: HomeAssistant) -> None:
    result = await _start_user_flow(hass)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_login_success_offers_the_sim_list(hass: HomeAssistant) -> None:
    client = _mock_client()

    with patch(CLIENT, return_value=client):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _login_input()
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "select_sim"
    assert list(result["data_schema"].schema["sim"].container) == [
        MOCK_SIM.display_name
    ]


async def test_selecting_a_sim_creates_the_entry(hass: HomeAssistant) -> None:
    client = _mock_client()

    with patch(CLIENT, return_value=client):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _login_input()
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"sim": MOCK_SIM.display_name}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == MOCK_SIM.display_name
    data = result["data"]
    assert data[CONF_SIM_META_ID] == "21919"
    assert data[CONF_ORDER_DETAIL_ID] == "59835"
    assert data[CONF_SIM_NUMBER] == "07700900000"
    assert data[CONF_ICCID] == "89443042334117134260"
    # The refreshed session must be stored, not the input cookie.
    assert data[CONF_SESSION_COOKIE] == "mozillion_session=abc; XSRF-TOKEN=xyz"
    assert data[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL


async def test_entry_is_unique_per_sim(hass: HomeAssistant) -> None:
    """Configuring the same SIM twice must abort, not duplicate."""
    client = _mock_client()

    with patch(CLIENT, return_value=client):
        first = await _start_user_flow(hass)
        first = await hass.config_entries.flow.async_configure(
            first["flow_id"], _login_input()
        )
        await hass.config_entries.flow.async_configure(
            first["flow_id"], {"sim": MOCK_SIM.display_name}
        )

        second = await _start_user_flow(hass)
        second = await hass.config_entries.flow.async_configure(
            second["flow_id"], _login_input()
        )
        second = await hass.config_entries.flow.async_configure(
            second["flow_id"], {"sim": MOCK_SIM.display_name}
        )

    assert second["type"] == data_entry_flow.FlowResultType.ABORT
    assert second["reason"] == "already_configured"


async def test_failed_login_shows_cannot_connect(hass: HomeAssistant) -> None:
    client = _mock_client(login_error=MozillionAuthError("bad password"))

    with patch(CLIENT, return_value=client):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _login_input()
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "cannot_connect"


async def test_no_credentials_and_no_cookie_shows_missing_auth(
    hass: HomeAssistant,
) -> None:
    client = _mock_client()

    with patch(CLIENT, return_value=client):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _login_input(**{CONF_EMAIL: "", CONF_PASSWORD: ""}),
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "missing_auth"


async def test_unreadable_sim_list_falls_back_to_manual_ids(
    hass: HomeAssistant,
) -> None:
    client = _mock_client(sims=[])

    with patch(CLIENT, return_value=client):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _login_input()
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "manual_ids"


# ---------------------------------------------------------------------------
# async_step_manual_ids
# ---------------------------------------------------------------------------


async def test_manual_ids_creates_the_entry(hass: HomeAssistant) -> None:
    client = _mock_client(sims=[])

    with patch(CLIENT, return_value=client):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _cookie_input()
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ORDER_DETAIL_ID: "59835",
                CONF_SIM_META_ID: "21919",
                CONF_SIM_NUMBER: "07700900000",
            },
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SIM_META_ID] == "21919"
    assert result["title"] == "Mozillion 07700900000"


async def test_manual_ids_are_validated_against_the_api(
    hass: HomeAssistant,
) -> None:
    client = _mock_client(sims=[], usage_error=RuntimeError("Unknown SIM"))

    with patch(CLIENT, return_value=client):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _cookie_input()
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ORDER_DETAIL_ID: "59835",
                CONF_SIM_META_ID: "21919",
                CONF_SIM_NUMBER: "",
            },
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "manual_ids"
    assert result["errors"]["base"] == "cannot_connect"


# ---------------------------------------------------------------------------
# async_step_reauth
# ---------------------------------------------------------------------------


async def test_reauth_updates_the_stored_session(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion 07700900000",
        data={**MOCK_ENTRY_DATA_COOKIE, CONF_SESSION_COOKIE: "stale=1"},
        unique_id="21919",
        version=2,
    )
    entry.add_to_hass(hass)
    client = _mock_client()

    with patch(CLIENT, return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
        )
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _credentials_only(_cookie_input())
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_rejects_a_failed_login(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion",
        data=dict(MOCK_ENTRY_DATA_COOKIE),
        unique_id="21919",
        version=2,
    )
    entry.add_to_hass(hass)
    client = _mock_client(login_error=MozillionAuthError("bad"))

    with patch(CLIENT, return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
        )
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _credentials_only(_login_input())
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def test_options_flow_updates_scan_interval(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion",
        data=dict(MOCK_ENTRY_DATA_COOKIE),
        unique_id="21919",
        version=2,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 7200}
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_SCAN_INTERVAL: 7200}


# ---------------------------------------------------------------------------
# async_step_reconfigure
# ---------------------------------------------------------------------------


async def test_reconfigure_switches_to_another_sim(hass: HomeAssistant) -> None:
    """Reconfiguring must update the entry in place, not create a second one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion 07700900000",
        data=dict(MOCK_ENTRY_DATA_COOKIE),
        unique_id="21919",
        version=2,
    )
    entry.add_to_hass(hass)

    other = MozillionSim(
        sim_meta_id="55555",
        order_detail_id="11111",
        sim_number="07700000001",
        plan_data_tariff="50GB",
    )
    client = _mock_client(sims=[MOCK_SIM, other])

    with patch(CLIENT, return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _credentials_only(_cookie_input())
        )
        assert result["step_id"] == "select_sim"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"sim": other.display_name}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert entry.data[CONF_SIM_META_ID] == "55555"
    assert entry.data[CONF_ORDER_DETAIL_ID] == "11111"
    assert entry.unique_id == "55555"
    assert entry.title == "07700000001 (50GB)"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reconfigure_rejects_a_failed_login(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion",
        data=dict(MOCK_ENTRY_DATA_COOKIE),
        unique_id="21919",
        version=2,
    )
    entry.add_to_hass(hass)
    client = _mock_client(login_error=MozillionAuthError("bad"))

    with patch(CLIENT, return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _credentials_only(_login_input())
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"
    assert result["errors"]["base"] == "cannot_connect"
