"""Tests for the Mozillion config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mozillion.config_flow import (
    MozillionConfigFlow,
    MozillionOptionsFlowHandler,
)
from custom_components.mozillion.const import (
    CONF_EMAIL,
    CONF_ORDER_DETAIL_ID,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_REMAINING_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_SIM_NUMBER,
    CONF_SIM_PLAN_ID,
    CONF_TOTP_SECRET,
    CONF_USAGE_KEY,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
    DEFAULT_REMAINING_KEY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USAGE_KEY,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _user_input_login(**overrides: Any) -> dict[str, Any]:
    """Build a default user input dict for the login path."""
    base = {
        CONF_EMAIL: "user@example.com",
        CONF_PASSWORD: "secret123",
        CONF_TOTP_SECRET: "",
        CONF_ORIGIN: DEFAULT_ORIGIN,
        CONF_SESSION_COOKIE: "",
        CONF_XSRF_TOKEN: "",
        CONF_USAGE_KEY: DEFAULT_USAGE_KEY,
        CONF_REMAINING_KEY: DEFAULT_REMAINING_KEY,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    }
    base.update(overrides)
    return base


def _user_input_cookie(**overrides: Any) -> dict[str, Any]:
    """Build a default user input dict for the cookie path."""
    base = {
        CONF_EMAIL: "",
        CONF_PASSWORD: "",
        CONF_TOTP_SECRET: "",
        CONF_ORIGIN: DEFAULT_ORIGIN,
        CONF_SESSION_COOKIE: "mozillion_session=abc; XSRF-TOKEN=xyz",
        CONF_XSRF_TOKEN: "xyz",
        CONF_USAGE_KEY: DEFAULT_USAGE_KEY,
        CONF_REMAINING_KEY: DEFAULT_REMAINING_KEY,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    }
    base.update(overrides)
    return base


def _make_flow(hass: MagicMock) -> MozillionConfigFlow:
    """Create a flow with a mocked hass."""
    flow = MozillionConfigFlow()
    flow.hass = hass
    return flow


# ---------------------------------------------------------------------------
# async_step_user – show form
# ---------------------------------------------------------------------------


class TestUserStepForm:
    """Tests for the initial user form display."""

    @pytest.mark.asyncio
    async def test_shows_form_on_first_call(self) -> None:
        """First call with no input should show form."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)
        result = await flow.async_step_user(user_input=None)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}


# ---------------------------------------------------------------------------
# async_step_user – login path
# ---------------------------------------------------------------------------


class TestUserStepLogin:
    """Tests for the login authentication path."""

    @pytest.mark.asyncio
    async def test_login_success_with_plans(self) -> None:
        """Successful login that finds plans goes to select_plan."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)

        mock_client = AsyncMock()
        mock_client.async_login.return_value = ("cookie=abc", "xsrf-tok")
        mock_client.async_fetch_dashboard_ids.return_value = [
            {
                "sim_plan_id": "sp-1",
                "order_detail_id": "od-1",
                "name": "07700900000",
                "sim_number": "07700900000",
            }
        ]

        with (
            patch("custom_components.mozillion.config_flow.async_get_clientsession"),
            patch(
                "custom_components.mozillion.config_flow.MozillionClient",
                return_value=mock_client,
            ),
        ):
            result = await flow.async_step_user(_user_input_login())

        # Should proceed to select_plan form
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_plan"

    @pytest.mark.asyncio
    async def test_login_success_no_plans_goes_to_manual(self) -> None:
        """Successful login with no plans goes to manual_ids."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)

        mock_client = AsyncMock()
        mock_client.async_login.return_value = ("cookie=abc", "xsrf-tok")
        mock_client.async_fetch_dashboard_ids.return_value = []

        with (
            patch("custom_components.mozillion.config_flow.async_get_clientsession"),
            patch(
                "custom_components.mozillion.config_flow.MozillionClient",
                return_value=mock_client,
            ),
        ):
            result = await flow.async_step_user(_user_input_login())

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_ids"

    @pytest.mark.asyncio
    async def test_login_failure_shows_error(self) -> None:
        """Failed login shows error on the form."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)

        mock_client = AsyncMock()
        mock_client.async_login.side_effect = RuntimeError("Bad credentials")

        with (
            patch("custom_components.mozillion.config_flow.async_get_clientsession"),
            patch(
                "custom_components.mozillion.config_flow.MozillionClient",
                return_value=mock_client,
            ),
        ):
            result = await flow.async_step_user(_user_input_login())

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        # Login failed → no cookie → flow reports missing_auth
        assert result["errors"]["base"] in ("cannot_connect", "missing_auth")


# ---------------------------------------------------------------------------
# async_step_user – cookie path
# ---------------------------------------------------------------------------


class TestUserStepCookie:
    """Tests for the cookie authentication path."""

    @pytest.mark.asyncio
    async def test_cookie_auth_goes_to_manual_ids(self) -> None:
        """Cookie auth (no email/password) goes to manual_ids."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)

        mock_client = AsyncMock()
        mock_client.async_fetch_dashboard_ids.return_value = []

        with (
            patch("custom_components.mozillion.config_flow.async_get_clientsession"),
            patch(
                "custom_components.mozillion.config_flow.MozillionClient",
                return_value=mock_client,
            ),
        ):
            result = await flow.async_step_user(_user_input_cookie())

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_ids"

    @pytest.mark.asyncio
    async def test_no_auth_at_all_shows_error(self) -> None:
        """No cookie and no login creds shows missing_auth error."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)

        with (
            patch("custom_components.mozillion.config_flow.async_get_clientsession"),
            patch(
                "custom_components.mozillion.config_flow.MozillionClient",
                return_value=AsyncMock(),
            ),
        ):
            result = await flow.async_step_user(
                _user_input_login(
                    **{CONF_EMAIL: "", CONF_PASSWORD: "", CONF_SESSION_COOKIE: ""}
                )
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "missing_auth"


# ---------------------------------------------------------------------------
# async_step_manual_ids
# ---------------------------------------------------------------------------


class TestManualIdsStep:
    """Tests for the manual ID entry step."""

    @pytest.mark.asyncio
    async def test_shows_form_on_first_call(self) -> None:
        """First call shows the manual IDs form."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)
        flow._credentials = _user_input_cookie()
        flow._cookie_header = "cookie=abc"
        flow._xsrf_token = "xsrf-tok"

        result = await flow.async_step_manual_ids(user_input=None)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_ids"

    @pytest.mark.asyncio
    async def test_valid_ids_creates_entry(self) -> None:
        """Valid IDs with successful validation creates entry."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)
        flow._credentials = _user_input_cookie()
        flow._cookie_header = "cookie=abc"
        flow._xsrf_token = "xsrf-tok"
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_create_entry = MagicMock(
            return_value={"type": FlowResultType.CREATE_ENTRY}
        )

        with patch(
            "custom_components.mozillion.config_flow._validate_input",
            new_callable=AsyncMock,
        ) as mock_validate:
            mock_validate.return_value = {}
            result = await flow.async_step_manual_ids(
                {
                    CONF_ORDER_DETAIL_ID: "order-1",
                    CONF_SIM_PLAN_ID: "sim-1",
                    CONF_SIM_NUMBER: "07700900000",
                }
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY

    @pytest.mark.asyncio
    async def test_validation_failure_shows_error(self) -> None:
        """Validation failure shows error."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)
        flow._credentials = _user_input_cookie()
        flow._cookie_header = "cookie=abc"
        flow._xsrf_token = "xsrf-tok"

        with patch(
            "custom_components.mozillion.config_flow._validate_input",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API down"),
        ):
            result = await flow.async_step_manual_ids(
                {
                    CONF_ORDER_DETAIL_ID: "order-1",
                    CONF_SIM_PLAN_ID: "sim-1",
                    CONF_SIM_NUMBER: "",
                }
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"


# ---------------------------------------------------------------------------
# async_step_select_plan
# ---------------------------------------------------------------------------


class TestSelectPlanStep:
    """Tests for the plan selection step."""

    @pytest.mark.asyncio
    async def test_shows_plan_dropdown(self) -> None:
        """Shows dropdown with available plans."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)
        flow._plans = [
            {
                "sim_plan_id": "sp-1",
                "order_detail_id": "od-1",
                "name": "07700900000",
                "sim_number": "07700900000",
            }
        ]

        result = await flow.async_step_select_plan(user_input=None)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_plan"

    @pytest.mark.asyncio
    async def test_selecting_plan_creates_entry(self) -> None:
        """Selecting a valid plan creates an entry."""
        hass = MagicMock(spec=HomeAssistant)
        flow = _make_flow(hass)
        flow._credentials = _user_input_login()
        flow._cookie_header = "cookie=abc"
        flow._xsrf_token = "xsrf-tok"
        flow._plans = [
            {
                "sim_plan_id": "sp-1",
                "order_detail_id": "od-1",
                "name": "07700900000",
                "sim_number": "07700900000",
            }
        ]
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_create_entry = MagicMock(
            return_value={"type": FlowResultType.CREATE_ENTRY}
        )

        with patch(
            "custom_components.mozillion.config_flow._validate_input",
            new_callable=AsyncMock,
            return_value={},
        ):
            result = await flow.async_step_select_plan(
                {"plan": "07700900000 (SIM: sp-1)"}
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class TestOptionsFlow:
    """Tests for the options flow handler."""

    @pytest.mark.asyncio
    async def test_shows_form_on_first_call(self) -> None:
        """Shows options form with current values."""
        handler = MozillionOptionsFlowHandler()
        entry = MagicMock()
        entry.options = {CONF_SCAN_INTERVAL: 3600}
        entry.data = {CONF_USAGE_KEY: "usedData", CONF_REMAINING_KEY: "totalData"}
        # Patch at the class level to bypass HA's deprecation guard
        with patch.object(
            type(handler),
            "config_entry",
            new_callable=lambda: property(lambda self: entry),
        ):
            result = await handler.async_step_init(user_input=None)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

    @pytest.mark.asyncio
    async def test_updates_options(self) -> None:
        """Submitting options creates entry."""
        handler = MozillionOptionsFlowHandler()
        handler.async_create_entry = MagicMock(
            return_value={"type": FlowResultType.CREATE_ENTRY}
        )

        result = await handler.async_step_init(
            {
                CONF_SCAN_INTERVAL: 7200,
                CONF_USAGE_KEY: "data.used",
                CONF_REMAINING_KEY: "data.total",
            }
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
