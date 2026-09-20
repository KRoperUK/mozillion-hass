"""Config flow for Mozillion integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MozillionClient, MozillionSim
from .const import (
    CONF_EMAIL,
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _credentials_schema(
    defaults: Mapping[str, Any] | None = None,
) -> dict[Any, Any]:
    """Build the shared credential fields used by setup and reauth."""

    defaults = defaults or {}

    def default(key: str) -> str:
        return str(defaults.get(key) or "")

    return {
        vol.Optional(CONF_EMAIL, default=default(CONF_EMAIL)): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Optional(CONF_PASSWORD, default=""): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Optional(
            CONF_TOTP_SECRET, default=default(CONF_TOTP_SECRET)
        ): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Optional(
            CONF_ORIGIN,
            default=default(CONF_ORIGIN) or DEFAULT_ORIGIN,
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
    }


async def _async_authenticate(
    client: MozillionClient, data: dict[str, Any]
) -> dict[str, Any]:
    """Resolve the session to use, logging in when credentials are supplied."""

    email = data.get(CONF_EMAIL)
    password = data.get(CONF_PASSWORD)

    if email and password:
        _LOGGER.debug("Authenticating with credentials for %s", email)
        cookie_header, xsrf_token = await client.async_login(
            email=email,
            password=password,
            totp_secret=data.get(CONF_TOTP_SECRET) or None,
            origin=data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
        )
        data[CONF_SESSION_COOKIE] = cookie_header
        if xsrf_token:
            data[CONF_XSRF_TOKEN] = xsrf_token

    if not data.get(CONF_SESSION_COOKIE):
        raise ValueError("missing_auth")

    return data


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate the entry by performing a single usage fetch."""

    client = MozillionClient(async_get_clientsession(hass))
    await client.async_get_usage(
        order_detail_id=data[CONF_ORDER_DETAIL_ID],
        sim_meta_id=data[CONF_SIM_META_ID],
        cookie_header=data.get(CONF_SESSION_COOKIE, ""),
        xsrf_token=data.get(CONF_XSRF_TOKEN),
    )
    _LOGGER.debug(
        "Validated usage fetch for order_detail_id=%s sim_meta_id=%s",
        data[CONF_ORDER_DETAIL_ID],
        data[CONF_SIM_META_ID],
    )


class MozillionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Mozillion."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize config flow."""
        super().__init__()
        self._credentials: dict[str, Any] = {}
        self._cookie_header: str | None = None
        self._xsrf_token: str | None = None
        self._sims: list[MozillionSim] = []
        # Set while reconfiguring an existing entry, so the assembled data
        # updates that entry instead of creating a second one.
        self._reconfigure_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial credentials step."""

        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.debug(
                "Mozillion config flow: user step with keys=%s", list(user_input)
            )
            client = MozillionClient(async_get_clientsession(self.hass))

            try:
                data = await _async_authenticate(client, dict(user_input))
            except ValueError:
                errors["base"] = "missing_auth"
            except RuntimeError:
                _LOGGER.exception("Login failed during user step")
                errors["base"] = "cannot_connect"
            else:
                self._credentials = data
                self._cookie_header = data.get(CONF_SESSION_COOKIE)
                self._xsrf_token = data.get(CONF_XSRF_TOKEN)

                try:
                    self._sims = await client.async_fetch_sims(
                        cookie_header=self._cookie_header or "",
                        xsrf_token=self._xsrf_token,
                    )
                except RuntimeError:
                    _LOGGER.exception(
                        "Failed to fetch the SIM list; falling back to manual entry"
                    )
                    self._sims = []

                if self._sims:
                    return await self.async_step_select_sim()
                return await self.async_step_manual_ids()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    **_credentials_schema(),
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
            ),
            errors=errors,
        )

    def _sim_choices(self) -> dict[str, MozillionSim]:
        """Return a label -> SIM mapping, keeping labels unique."""

        choices: dict[str, MozillionSim] = {}
        for sim in self._sims:
            label = sim.display_name
            if label in choices:
                label = f"{label} [{sim.sim_meta_id}]"
            choices[label] = sim
        return choices

    async def async_step_select_sim(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user pick which SIM to track."""

        choices = self._sim_choices()
        if not choices:
            return await self.async_step_manual_ids()

        errors: dict[str, str] = {}

        if user_input is not None:
            sim = choices.get(user_input["sim"])
            if sim is None:
                errors["base"] = "cannot_connect"
            else:
                data = {
                    **self._credentials,
                    CONF_ORDER_DETAIL_ID: sim.order_detail_id,
                    CONF_SIM_META_ID: sim.sim_meta_id,
                    CONF_SIM_NUMBER: sim.sim_number,
                    CONF_ICCID: sim.iccid,
                    CONF_SESSION_COOKIE: self._cookie_header or "",
                    CONF_XSRF_TOKEN: self._xsrf_token or "",
                }
                result = await self._async_create_entry(data, title=sim.display_name)
                if result is not None:
                    return result
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="select_sim",
            data_schema=vol.Schema({vol.Required("sim"): vol.In(list(choices))}),
            errors=errors,
        )

    async def async_step_manual_ids(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manual entry of the order and SIM ids."""

        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.debug("Mozillion config flow: manual_ids keys=%s", list(user_input))
            data = {
                **self._credentials,
                CONF_ORDER_DETAIL_ID: user_input[CONF_ORDER_DETAIL_ID],
                CONF_SIM_META_ID: user_input[CONF_SIM_META_ID],
                CONF_SIM_NUMBER: user_input.get(CONF_SIM_NUMBER, ""),
                CONF_SESSION_COOKIE: self._cookie_header or "",
                CONF_XSRF_TOKEN: self._xsrf_token or "",
            }
            title = (
                f"Mozillion {data[CONF_SIM_NUMBER]}"
                if data[CONF_SIM_NUMBER]
                else "Mozillion"
            )
            result = await self._async_create_entry(data, title=title)
            if result is not None:
                return result
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual_ids",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ORDER_DETAIL_ID): str,
                    vol.Required(CONF_SIM_META_ID): str,
                    vol.Optional(CONF_SIM_NUMBER, default=""): str,
                }
            ),
            errors=errors,
        )

    async def _async_create_entry(
        self, data: dict[str, Any], title: str
    ) -> config_entries.ConfigFlowResult | None:
        """Validate the assembled entry and create it.

        Returns ``None`` when validation fails so the caller can re-show its
        own form with an error.
        """

        try:
            await _validate_input(self.hass, data)
        except RuntimeError:
            _LOGGER.exception("Validation failed while creating the config entry")
            return None

        if self._reconfigure_entry is not None:
            # Reconfigure in place: the entry id, its entities and its history
            # are all preserved.
            return self.async_update_reload_and_abort(
                self._reconfigure_entry,
                data=data,
                title=title,
                unique_id=data[CONF_SIM_META_ID],
            )

        await self.async_set_unique_id(data[CONF_SIM_META_ID])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    async def async_step_reconfigure(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start reconfiguration of an existing entry."""

        self._reconfigure_entry = self._get_reconfigure_entry()
        return await self.async_step_reconfigure_confirm()

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-authenticate, then let the SIM be chosen again."""

        entry = self._reconfigure_entry or self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            client = MozillionClient(async_get_clientsession(self.hass))
            try:
                data = await _async_authenticate(client, dict(user_input))
            except ValueError:
                errors["base"] = "missing_auth"
            except RuntimeError:
                _LOGGER.exception("Reconfigure login failed")
                errors["base"] = "cannot_connect"
            else:
                self._credentials = data
                self._cookie_header = data.get(CONF_SESSION_COOKIE)
                self._xsrf_token = data.get(CONF_XSRF_TOKEN)
                try:
                    self._sims = await client.async_fetch_sims(
                        cookie_header=self._cookie_header or "",
                        xsrf_token=self._xsrf_token,
                    )
                except RuntimeError:
                    _LOGGER.exception("Failed to fetch the SIM list")
                    self._sims = []

                if self._sims:
                    return await self.async_step_select_sim()
                return await self.async_step_manual_ids()

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=vol.Schema(_credentials_schema(entry.data)),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle re-authentication when the session expires.

        Home Assistant calls this with the entry's stored data, so the form is
        always shown first — treating that argument as a submission would
        silently re-submit the credentials that just failed.
        """

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect fresh credentials or a fresh session cookie."""

        errors: dict[str, str] = {}
        existing = self._get_reauth_entry()

        if user_input is not None:
            client = MozillionClient(async_get_clientsession(self.hass))
            try:
                updated = await _async_authenticate(client, dict(user_input))
            except ValueError:
                errors["base"] = "missing_auth"
            except RuntimeError:
                _LOGGER.exception("Reauth login failed")
                errors["base"] = "cannot_connect"
            else:
                new_data = {
                    **existing.data,
                    CONF_EMAIL: updated.get(CONF_EMAIL)
                    or existing.data.get(CONF_EMAIL, ""),
                    CONF_PASSWORD: updated.get(CONF_PASSWORD)
                    or existing.data.get(CONF_PASSWORD, ""),
                    CONF_TOTP_SECRET: updated.get(CONF_TOTP_SECRET)
                    or existing.data.get(CONF_TOTP_SECRET, ""),
                    CONF_ORIGIN: updated.get(CONF_ORIGIN)
                    or existing.data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
                    CONF_SESSION_COOKIE: updated.get(CONF_SESSION_COOKIE, ""),
                    CONF_XSRF_TOKEN: updated.get(CONF_XSRF_TOKEN, ""),
                }
                try:
                    await _validate_input(self.hass, new_data)
                except RuntimeError:
                    _LOGGER.exception("Reauth validation failed")
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(
                        existing,
                        data=new_data,
                        reason="reauth_successful",
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_credentials_schema(existing.data)),
            errors=errors,
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
        """Manage the polling interval."""

        if user_input is not None:
            return self.async_create_entry(title="Options", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current): int,
                }
            ),
        )
