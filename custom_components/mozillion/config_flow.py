"""Config flow for Mozillion integration.

The config entry is the account, so the main flow collects credentials only. SIMs are
added as subentries, either alongside a brand new account or later from the integration
page, which is what lets one set of credentials cover every SIM.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MozillionAuthError, MozillionClient, MozillionSim
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
    SUBENTRY_TYPE_SIM,
)
from .session import has_credentials

_LOGGER = logging.getLogger(__name__)


def _credentials_schema(defaults: Mapping[str, Any] | None = None) -> dict[Any, Any]:
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


def _sim_subentry_data(sim: MozillionSim) -> dict[str, Any]:
    """Build the payload stored on a SIM's subentry."""

    return {
        CONF_ORDER_DETAIL_ID: sim.order_detail_id,
        CONF_SIM_META_ID: sim.sim_meta_id,
        CONF_SIM_NUMBER: sim.sim_number,
        CONF_ICCID: sim.iccid,
    }


def _sim_choices(sims: list[MozillionSim], taken: set[str]) -> dict[str, MozillionSim]:
    """Return a label -> SIM mapping, excluding SIMs already tracked."""

    choices: dict[str, MozillionSim] = {}
    for sim in sims:
        if sim.sim_meta_id in taken:
            continue
        label = sim.display_name
        if label in choices:
            label = f"{label} [{sim.sim_meta_id}]"
        choices[label] = sim
    return choices


async def _async_client_with_session(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[MozillionClient, str, str | None]:
    """Return a client and working cookies for the account.

    The stored session is tried first; a stale one is renewed from the entry's
    credentials, the same way the coordinator recovers.
    """

    client = MozillionClient(async_get_clientsession(hass))
    cookie_header = entry.data.get(CONF_SESSION_COOKIE) or ""
    xsrf_token = entry.data.get(CONF_XSRF_TOKEN)

    async def log_in() -> tuple[str, str | None]:
        return await client.async_login(
            email=entry.data[CONF_EMAIL],
            password=entry.data[CONF_PASSWORD],
            totp_secret=entry.data.get(CONF_TOTP_SECRET) or None,
            origin=entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
        )

    if not cookie_header and has_credentials(entry):
        cookie_header, xsrf_token = await log_in()
        return client, cookie_header, xsrf_token

    try:
        await client.async_fetch_sims(
            cookie_header=cookie_header, xsrf_token=xsrf_token
        )
    except MozillionAuthError:
        if not has_credentials(entry):
            raise
        _LOGGER.debug("Stored session expired; logging in to list SIMs")
        cookie_header, xsrf_token = await log_in()

    return client, cookie_header, xsrf_token


async def _async_validate(
    client: MozillionClient,
    cookie_header: str,
    xsrf_token: str | None,
    sim: MozillionSim,
) -> None:
    """Prove the credentials can read this SIM's usage before saving it."""

    await client.async_get_usage(
        order_detail_id=sim.order_detail_id,
        sim_meta_id=sim.sim_meta_id,
        cookie_header=cookie_header,
        xsrf_token=xsrf_token,
    )


class MozillionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the account-level config flow."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialize config flow."""
        super().__init__()
        self._credentials: dict[str, Any] = {}
        self._sims: list[MozillionSim] = []
        self._cookie_header: str | None = None
        self._xsrf_token: str | None = None
        self._reconfigure_entry: ConfigEntry | None = None

    # Must be a classmethod: HA looks the handler up in HANDLERS and calls this
    # on the class, so an instance method raised
    #   TypeError: ... missing 1 required positional argument: 'config_entry'
    # the moment HA enumerated the supported subentry types.
    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""

        return {SUBENTRY_TYPE_SIM: SimSubentryFlowHandler}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect the account credentials and find its SIMs."""

        errors: dict[str, str] = {}

        if user_input is not None:
            client = MozillionClient(async_get_clientsession(self.hass))
            try:
                data = await _async_authenticate(client, dict(user_input))
            except ValueError:
                errors["base"] = "missing_auth"
            except RuntimeError:
                _LOGGER.exception("Login failed during user step")
                errors["base"] = "cannot_connect"
            else:
                # One entry per account: further SIMs are added to this one.
                email = str(data.get(CONF_EMAIL) or "").strip().lower()
                if email:
                    await self.async_set_unique_id(email)
                    self._abort_if_unique_id_configured()

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

    async def async_step_select_sim(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick the first SIM this account should track."""

        choices = _sim_choices(self._sims, taken=set())
        if not choices:
            return await self.async_step_manual_ids()

        errors: dict[str, str] = {}

        if user_input is not None:
            sim = choices.get(user_input["sim"])
            if sim is not None and await self._async_validate_sim(sim):
                return self._create_account(sim)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="select_sim",
            data_schema=vol.Schema({vol.Required("sim"): vol.In(list(choices))}),
            errors=errors,
        )

    async def async_step_manual_ids(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Enter the first SIM's ids by hand when the list cannot be read."""

        errors: dict[str, str] = {}

        if user_input is not None:
            sim = MozillionSim(
                sim_meta_id=str(user_input[CONF_SIM_META_ID]),
                order_detail_id=str(user_input[CONF_ORDER_DETAIL_ID]),
                sim_number=str(user_input.get(CONF_SIM_NUMBER) or ""),
            )
            if await self._async_validate_sim(sim):
                return self._create_account(sim)
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

    async def _async_validate_sim(self, sim: MozillionSim) -> bool:
        """Check this session can actually read the SIM before saving it."""

        try:
            await _async_validate(
                MozillionClient(async_get_clientsession(self.hass)),
                self._cookie_header or "",
                self._xsrf_token,
                sim,
            )
        except RuntimeError:
            _LOGGER.exception("Validation failed for the selected SIM")
            return False
        return True

    def _create_account(self, sim: MozillionSim) -> config_entries.ConfigFlowResult:
        """Create the account entry with its first SIM as a subentry."""

        data = {
            key: value
            for key, value in self._credentials.items()
            if key not in (CONF_SCAN_INTERVAL,)
        }
        data[CONF_SESSION_COOKIE] = self._cookie_header or ""
        data[CONF_XSRF_TOKEN] = self._xsrf_token or ""

        options = {CONF_SCAN_INTERVAL: self._credentials.get(CONF_SCAN_INTERVAL)}
        # A form always supplies it, but a config.yaml import may not.
        if options[CONF_SCAN_INTERVAL] is None:
            options[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL

        return self.async_create_entry(
            title=_account_title(data),
            data=data,
            options=options,
            subentries=[
                {
                    "subentry_type": SUBENTRY_TYPE_SIM,
                    "data": _sim_subentry_data(sim),
                    "title": sim.display_name,
                    "unique_id": sim.sim_meta_id,
                }
            ],
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle re-authentication when the session expires.

        Home Assistant calls this with the entry's stored data, so the form is always
        shown first -- treating that argument as a submission would silently
        re-submit the credentials that just failed.
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
                if await self._async_validate_entry(existing, new_data):
                    return self.async_update_reload_and_abort(
                        existing,
                        data=new_data,
                        reason="reauth_successful",
                    )
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_credentials_schema(existing.data)),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start reconfiguration of an existing account's credentials."""

        self._reconfigure_entry = self._get_reconfigure_entry()
        return await self.async_step_reconfigure_confirm()

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-authenticate the account. SIMs are changed on their own subentry."""

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
                new_data = {
                    **entry.data,
                    CONF_EMAIL: data.get(CONF_EMAIL) or entry.data.get(CONF_EMAIL, ""),
                    CONF_PASSWORD: data.get(CONF_PASSWORD)
                    or entry.data.get(CONF_PASSWORD, ""),
                    CONF_TOTP_SECRET: data.get(CONF_TOTP_SECRET)
                    or entry.data.get(CONF_TOTP_SECRET, ""),
                    CONF_ORIGIN: data.get(CONF_ORIGIN)
                    or entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
                    CONF_SESSION_COOKIE: data.get(CONF_SESSION_COOKIE, ""),
                    CONF_XSRF_TOKEN: data.get(CONF_XSRF_TOKEN, ""),
                }
                if await self._async_validate_entry(entry, new_data):
                    if entry.version != self.VERSION:
                        self.hass.config_entries.async_update_entry(
                            entry, version=self.VERSION
                        )
                    return self.async_update_reload_and_abort(
                        entry, data=new_data, title=_account_title(new_data)
                    )
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=vol.Schema(_credentials_schema(entry.data)),
            errors=errors,
        )

    async def _async_validate_entry(
        self, entry: ConfigEntry, data: dict[str, Any]
    ) -> bool:
        """Check the new session by reading one of the account's SIMs."""

        sims = [
            subentry
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_SIM
        ]
        if not sims:
            return True
        subentry = sims[0]
        try:
            await _async_validate(
                MozillionClient(async_get_clientsession(self.hass)),
                data.get(CONF_SESSION_COOKIE, ""),
                data.get(CONF_XSRF_TOKEN),
                MozillionSim(
                    sim_meta_id=subentry.data[CONF_SIM_META_ID],
                    order_detail_id=subentry.data[CONF_ORDER_DETAIL_ID],
                ),
            )
        except RuntimeError:
            _LOGGER.exception("Validation failed while saving the account")
            return False
        return True

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle import from configuration.yaml."""

        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MozillionOptionsFlowHandler:
        return MozillionOptionsFlowHandler()


class SimSubentryFlowHandler(ConfigSubentryFlow):
    """Add or reconfigure one SIM on an existing account."""

    # Set by the entry point each step runs from, so the shared body knows whether
    # it is creating a subentry or updating one.
    _reconfiguring: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a SIM to this account."""

        return await self._async_step_sim(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Move this subentry to a different SIM."""

        return await self._async_step_sim(user_input, reconfigure=True)

    async def async_step_manual_ids(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Enter a SIM's ids by hand when the dashboard cannot be read."""

        errors: dict[str, str] = {}

        if user_input is not None:
            sim = MozillionSim(
                sim_meta_id=str(user_input[CONF_SIM_META_ID]),
                order_detail_id=str(user_input[CONF_ORDER_DETAIL_ID]),
                sim_number=str(user_input.get(CONF_SIM_NUMBER) or ""),
            )
            if await self._async_finish(sim):
                return self._result_for(sim, self._reconfiguring)
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

    async def _async_step_sim(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> SubentryFlowResult:
        """Shared body of the add and reconfigure steps."""

        self._reconfiguring = reconfigure
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        try:
            client, cookie_header, xsrf_token = await _async_client_with_session(
                self.hass, entry
            )
            sims = await client.async_fetch_sims(
                cookie_header=cookie_header, xsrf_token=xsrf_token
            )
        except RuntimeError:
            _LOGGER.exception("Could not read the Mozillion SIM list")
            return await self.async_step_manual_ids()

        tracked = {
            subentry.data.get(CONF_SIM_META_ID)
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_SIM
            and subentry.subentry_id != self._reconfigure_subentry_id
        }
        choices = _sim_choices(sims, taken={t for t in tracked if t})
        if not choices:
            return self.async_abort(reason="no_new_sims")

        errors: dict[str, str] = {}
        if user_input is not None:
            sim = choices.get(user_input["sim"])
            if sim is None:
                errors["base"] = "cannot_connect"
            elif await self._async_finish(sim):
                return self._result_for(sim, reconfigure)
            else:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("sim"): vol.In(list(choices))}),
            errors=errors,
        )

    async def _async_finish(self, sim: MozillionSim) -> bool:
        """Validate the SIM against the API before saving it."""

        entry = self._get_entry()
        try:
            client, cookie_header, xsrf_token = await _async_client_with_session(
                self.hass, entry
            )
            await _async_validate(client, cookie_header, xsrf_token, sim)
        except RuntimeError:
            _LOGGER.exception("Validation failed for the selected SIM")
            return False
        return True

    def _result_for(self, sim: MozillionSim, reconfigure: bool) -> SubentryFlowResult:
        """Create the subentry, or update it in place when reconfiguring."""

        data = _sim_subentry_data(sim)
        if reconfigure:
            entry = self._get_entry()
            return self.async_update_and_abort(
                entry,
                self._get_reconfigure_subentry(),
                title=sim.display_name,
                data=data,
                unique_id=sim.sim_meta_id,
            )
        return self.async_create_entry(
            title=sim.display_name, data=data, unique_id=sim.sim_meta_id
        )


def _account_title(data: Mapping[str, Any]) -> str:
    """Name the account entry after the login, falling back to the domain name."""

    return str(data.get(CONF_EMAIL) or "Mozillion")


class MozillionOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Mozillion options. The scan interval is account-wide."""

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
                {vol.Optional(CONF_SCAN_INTERVAL, default=current): int}
            ),
        )
