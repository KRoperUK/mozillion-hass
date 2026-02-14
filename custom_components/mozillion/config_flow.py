"""Config flow for Mozillion integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import MozillionClient
from .const import (
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
    DEFAULT_REMAINING_KEY,
    DEFAULT_ORIGIN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USAGE_KEY,
    DOMAIN,
)


_LOGGER = logging.getLogger(__name__)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input by performing a single fetch."""

    session = async_get_clientsession(hass)
    client = MozillionClient(session)
    cookie_header = data.get(CONF_SESSION_COOKIE)
    xsrf_token = data.get(CONF_XSRF_TOKEN)

    email = data.get(CONF_EMAIL)
    password = data.get(CONF_PASSWORD)
    totp_secret = data.get(CONF_TOTP_SECRET) or None
    origin = data.get(CONF_ORIGIN, DEFAULT_ORIGIN)

    if email and password:
        _LOGGER.debug("Config flow validating login for %s", email)
        cookie_header, xsrf_token = await client.async_login(
            email=email,
            password=password,
            totp_secret=totp_secret,
            origin=origin,
        )
        data[CONF_SESSION_COOKIE] = cookie_header
        if xsrf_token:
            data[CONF_XSRF_TOKEN] = xsrf_token

    if not cookie_header:
        raise ValueError("missing_auth")

    await client.async_get_usage(
        order_detail_id=data[CONF_ORDER_DETAIL_ID],
        sim_plan_id=data[CONF_SIM_PLAN_ID],
        cookie_header=cookie_header,
        xsrf_token=xsrf_token,
    )
    _LOGGER.debug(
        "Validated usage fetch for order_detail_id=%s sim_plan_id=%s",
        data[CONF_ORDER_DETAIL_ID],
        data[CONF_SIM_PLAN_ID],
    )
    return data


class MozillionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mozillion."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._credentials: dict[str, Any] = {}
        self._cookie_header: str | None = None
        self._xsrf_token: str | None = None
        self._plans: list[dict[str, str]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.warning(
                "Mozillion config flow: user step with keys=%s", list(user_input)
            )

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = MozillionClient(session)

            email = user_input.get(CONF_EMAIL)
            password = user_input.get(CONF_PASSWORD)
            totp_secret = user_input.get(CONF_TOTP_SECRET) or None
            origin = user_input.get(CONF_ORIGIN, DEFAULT_ORIGIN)
            cookie_header = user_input.get(CONF_SESSION_COOKIE)
            xsrf_token = user_input.get(CONF_XSRF_TOKEN)

            # Try login if credentials provided
            if email and password:
                try:
                    cookie_header, xsrf_token = await client.async_login(
                        email=email,
                        password=password,
                        totp_secret=totp_secret,
                        origin=origin,
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Login failed during user step")
                    errors["base"] = "cannot_connect"

            if not errors and cookie_header:
                # Store credentials and cookies for next step
                self._credentials = user_input
                self._cookie_header = cookie_header
                self._xsrf_token = xsrf_token

                # Try to fetch plans automatically
                try:
                    self._plans = await client.async_fetch_dashboard_ids(
                        cookie_header=cookie_header,
                        xsrf_token=xsrf_token,
                    )
                    _LOGGER.debug("Fetched %d plans from dashboard", len(self._plans))
                except Exception:  # noqa: BLE001
                    # If fetch fails, continue to manual entry
                    _LOGGER.exception(
                        "Failed to fetch plans; falling back to manual IDs"
                    )
                    self._plans = []

                if self._plans:
                    # Go to plan selection
                    return await self.async_step_select_plan()
                else:
                    # No plans found, go to manual entry
                    return await self.async_step_manual_ids()
            elif not cookie_header:
                _LOGGER.debug("No cookie header available after user step")
                errors["base"] = "missing_auth"

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_EMAIL, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
                ),
                vol.Optional(CONF_PASSWORD, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_TOTP_SECRET, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_ORIGIN,
                    default=DEFAULT_ORIGIN,
                    description={"advanced": True},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                ),
                vol.Optional(
                    CONF_SESSION_COOKIE,
                    default="",
                    description={"advanced": True},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT, multiline=True
                    )
                ),
                vol.Optional(
                    CONF_XSRF_TOKEN,
                    default="",
                    description={"advanced": True},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_USAGE_KEY,
                    default=DEFAULT_USAGE_KEY,
                    description={"advanced": True},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_REMAINING_KEY,
                    default=DEFAULT_REMAINING_KEY,
                    description={"advanced": True},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=DEFAULT_SCAN_INTERVAL,
                    description={"advanced": True},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX, min=60
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_select_plan(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let user select from available plans."""

        if user_input is not None:
            _LOGGER.warning("Mozillion config flow: select_plan input=%s", user_input)
            selected = user_input["plan"]
            # Find the selected plan
            plan = next(
                (
                    p
                    for p in self._plans
                    if f"{p['name']} (SIM: {p['sim_plan_id']})" == selected
                ),
                None,
            )
            if plan:
                # Merge with stored credentials
                data = {**self._credentials}
                data[CONF_ORDER_DETAIL_ID] = plan["order_detail_id"]
                data[CONF_SIM_PLAN_ID] = plan["sim_plan_id"]
                data[CONF_SIM_NUMBER] = plan.get("sim_number", "")
                data[CONF_SESSION_COOKIE] = self._cookie_header
                if self._xsrf_token:
                    data[CONF_XSRF_TOKEN] = self._xsrf_token

                # Validate the selection
                try:
                    await _validate_input(self.hass, data)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Validation failed after plan selection; redirecting to manual IDs"
                    )
                    return await self.async_step_manual_ids({"error": "cannot_connect"})

                await self.async_set_unique_id(data[CONF_ORDER_DETAIL_ID])
                self._abort_if_unique_id_configured()
                # Title defaults to plan name when available
                name = data.get(CONF_NAME, plan["name"])
                return self.async_create_entry(title=name, data=data)
            _LOGGER.debug("Plan selection not found in available plans")

        # Build dropdown options
        plan_options = [f"{p['name']} (SIM: {p['sim_plan_id']})" for p in self._plans]

        data_schema = vol.Schema(
            {
                vol.Required("plan"): vol.In(plan_options),
            }
        )

        return self.async_show_form(step_id="select_plan", data_schema=data_schema)

    async def async_step_manual_ids(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manual entry of order and SIM plan IDs."""

        errors: dict[str, str] = {}
        if user_input and user_input.get("error"):
            errors["base"] = user_input["error"]

        if user_input is not None and not errors:
            _LOGGER.warning(
                "Mozillion config flow: manual_ids keys=%s", list(user_input)
            )
            # Merge with stored credentials
            data = {**self._credentials}
            data[CONF_ORDER_DETAIL_ID] = user_input[CONF_ORDER_DETAIL_ID]
            data[CONF_SIM_PLAN_ID] = user_input[CONF_SIM_PLAN_ID]
            data[CONF_SIM_NUMBER] = user_input.get(CONF_SIM_NUMBER, "")
            data[CONF_SESSION_COOKIE] = self._cookie_header
            if self._xsrf_token:
                data[CONF_XSRF_TOKEN] = self._xsrf_token

            # Validate
            try:
                await _validate_input(self.hass, data)
            except ValueError as err:
                _LOGGER.debug("Validation error in manual IDs: %s", err)
                errors["base"] = str(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating manual IDs")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(data[CONF_ORDER_DETAIL_ID])
                self._abort_if_unique_id_configured()
                # Title defaults to a generic label when not specified
                name = data.get(CONF_NAME, "Mozillion")
                return self.async_create_entry(title=name, data=data)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ORDER_DETAIL_ID): str,
                vol.Required(CONF_SIM_PLAN_ID): str,
                vol.Optional(CONF_SIM_NUMBER, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="manual_ids", data_schema=data_schema, errors=errors
        )

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle import from configuration.yaml."""

        return await self.async_step_user(user_input)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return MozillionOptionsFlowHandler()


class MozillionOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Mozillion options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(title="Options", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): int,
                vol.Optional(
                    CONF_USAGE_KEY,
                    default=self.config_entry.data.get(
                        CONF_USAGE_KEY, DEFAULT_USAGE_KEY
                    ),
                ): str,
                vol.Optional(
                    CONF_REMAINING_KEY,
                    default=self.config_entry.data.get(
                        CONF_REMAINING_KEY, DEFAULT_REMAINING_KEY
                    ),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=data_schema, errors=errors
        )
