"""Tests for the Mozillion config flow, using the real Home Assistant harness."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.mozillion.api import MozillionAuthError
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
    SUBENTRY_TYPE_SIM,
)
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import MOCK_ENTRY_DATA_COOKIE, MOCK_SIM, _make_config_entry

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
    # The entry is the account and the SIM is a subentry of it, so the credentials
    # live once however many SIMs the account ends up tracking.
    assert result["title"] == "user@example.com"
    data = result["data"]
    # The refreshed session must be stored, not the input cookie.
    assert data[CONF_SESSION_COOKIE] == "mozillion_session=abc; XSRF-TOKEN=xyz"
    assert result["options"] == {CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL}
    for sim_key in (CONF_SIM_META_ID, CONF_ORDER_DETAIL_ID, CONF_SIM_NUMBER):
        assert sim_key not in data, f"{sim_key} belongs on the SIM, not the account"

    subentries = result["subentries"]
    assert len(subentries) == 1
    subentry = subentries[0]
    assert subentry["subentry_type"] == SUBENTRY_TYPE_SIM
    assert subentry["title"] == MOCK_SIM.display_name
    assert subentry["unique_id"] == "7654321"
    assert subentry["data"][CONF_ORDER_DETAIL_ID] == "1234567"
    assert subentry["data"][CONF_SIM_NUMBER] == "07700900000"
    assert subentry["data"][CONF_ICCID] == "89440000000000000000"


async def test_entry_is_unique_per_account(hass: HomeAssistant) -> None:
    """Adding the same account twice aborts; further SIMs go on the first entry."""
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

    # The account is recognised at the login step, before any SIM is chosen: the
    # extra SIM belongs on the entry that already exists.
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
                CONF_ORDER_DETAIL_ID: "1234567",
                CONF_SIM_META_ID: "7654321",
                CONF_SIM_NUMBER: "07700900000",
            },
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    subentry = result["subentries"][0]
    assert subentry["data"][CONF_SIM_META_ID] == "7654321"
    assert subentry["data"][CONF_ORDER_DETAIL_ID] == "1234567"
    assert subentry["title"] == "07700900000"


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
                CONF_ORDER_DETAIL_ID: "1234567",
                CONF_SIM_META_ID: "7654321",
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
        unique_id="7654321",
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
        unique_id="7654321",
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
        unique_id="7654321",
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


async def test_reconfigure_updates_credentials_and_keeps_the_sims(
    hass: HomeAssistant,
) -> None:
    """Reconfiguring the account must not disturb the SIMs hanging off it.

    Switching which SIM is tracked is the SIM subentry's own reconfigure step, so
    this one only deals with credentials.
    """
    entry = _make_config_entry(
        data={
            **MOCK_ENTRY_DATA_COOKIE,
            CONF_EMAIL: "old@example.com",
            CONF_PASSWORD: "old-secret",
        }
    )
    entry.add_to_hass(hass)
    subentries_before = dict(entry.subentries)

    with patch(CLIENT, return_value=_mock_client()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["step_id"] == "reconfigure_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _credentials_only(_login_input())
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_EMAIL] == "user@example.com"
    assert entry.data[CONF_PASSWORD] == "secret123"
    assert dict(entry.subentries) == subentries_before
    assert entry.version == 3


async def test_reconfigure_rejects_a_failed_login(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion",
        data=dict(MOCK_ENTRY_DATA_COOKIE),
        unique_id="7654321",
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


async def test_subentry_types_hook_is_callable_on_the_handler_class(
    hass: HomeAssistant,
) -> None:
    """Home Assistant calls this hook on the class, not on an instance.

    HA looks the handler up in ``HANDLERS`` and calls
    ``handler.async_get_supported_subentry_types(entry)``. Declaring it as an
    instance method bound the entry to ``self`` and raised::

        TypeError: ... missing 1 required positional argument: 'config_entry'

    so the supported subentry types could not be enumerated at all. This calls it
    the way HA does -- on the class, not through a flow instance -- which is the
    path the rest of the suite never exercised.
    """

    # Importing the module is what registers the domain, which happens at class
    # definition time -- so the registry is empty until something imports it.
    from custom_components.mozillion.config_flow import MozillionConfigFlow

    handler = config_entries.HANDLERS.get(DOMAIN)
    assert handler is MozillionConfigFlow, "HA resolves the handler through HANDLERS"

    supported = handler.async_get_supported_subentry_types(_make_config_entry())

    assert SUBENTRY_TYPE_SIM in supported
    assert supported[SUBENTRY_TYPE_SIM] is not None
