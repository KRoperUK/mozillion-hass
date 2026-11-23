"""Config flow for Mozillion integration.

The config entry is the account, so the main flow collects credentials only. SIMs are
added as subentries, either alongside a brand new account or later from the integration
page, which is what lets one set of credentials cover every SIM.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

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
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from ._compat import schema
from .api import (
    MozillionAuthError,
    MozillionChallengeError,
    MozillionClient,
    MozillionSim,
    MozillionSolverError,
    MozillionTotpSecretError,
    MozillionTwoFactorRejected,
    MozillionTwoFactorRequired,
)
from .const import (
    CONF_EMAIL,
    CONF_FLARESOLVERR_URL,
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

# Mozillion answers a fixed number of requests per window, so a shorter interval
# spends the budget and every poll comes back through the rate-limit path in
# ``api._raise_for_rate_limit``. The setup and options dialogs enforce this same
# floor; the options dialog used to accept anything, including zero.
MIN_SCAN_INTERVAL = 60


def _dedicated_client(
    hass: HomeAssistant, flaresolverr_url: str | None = None
) -> MozillionClient:
    """Build a client on a session the caller owns.

    The session deliberately does not come from ``async_get_clientsession``:
    Mozillion's login cookies must stay out of the jar every integration shares
    (``quality_scale.yaml``, ``inject-websession``).

    It also cannot hang off a config entry. Home Assistant only ties a session to
    the entry's unload when it is created *during* entry setup --
    ``_async_register_clientsession_shutdown`` returns early when
    ``config_entries.current_entry`` is unset -- so a session created inside a
    flow is registered for ``EVENT_HOMEASSISTANT_CLOSE`` only. A flow therefore
    keeps one for its whole life instead of one per step; see
    ``MozillionConfigFlow._async_client``.
    """

    return MozillionClient(
        async_create_clientsession(hass), flaresolverr_url=flaresolverr_url or None
    )


# Errors that mean "ask for (or re-ask for) the 2FA secret" rather than "start over".
_TOTP_ERRORS = frozenset({"two_factor_required", "invalid_totp"})
# Errors fixed by changing how we reach Mozillion, not by changing a credential.
_BLOCKED_ERRORS = frozenset({"cloudflare_blocked", "solver_failed"})


def _suggest(value: Any) -> dict[str, Any]:
    return {"suggested_value": str(value or "")}


def _login_fields(
    defaults: Mapping[str, Any] | None = None, *, required: bool
) -> dict[Any, Any]:
    """Email, password and the origin header: the fields a login needs."""

    defaults = defaults or {}
    marker = schema.Required if required else schema.Optional
    extra: dict[str, Any] = {} if required else {"default": ""}
    return {
        marker(
            CONF_EMAIL, description=_suggest(defaults.get(CONF_EMAIL)), **extra
        ): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        marker(CONF_PASSWORD, **extra): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        schema.Optional(
            CONF_ORIGIN,
            default=str(defaults.get(CONF_ORIGIN) or DEFAULT_ORIGIN),
            description={"advanced": True},
        ): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        ),
    }


def _solver_field(default: str = "") -> dict[Any, Any]:
    """The FlareSolverr address, offered once Cloudflare has blocked a login."""

    return {
        schema.Optional(
            CONF_FLARESOLVERR_URL, description=_suggest(default)
        ): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        )
    }


def _session_fields(*, required: bool, advanced: bool) -> dict[Any, Any]:
    """The cookie header and XSRF token copied from a signed-in browser."""

    marker = schema.Required if required else schema.Optional
    extra: dict[str, Any] = {} if required else {"default": ""}
    description = {"advanced": advanced}
    return {
        marker(
            CONF_SESSION_COOKIE, description=description, **extra
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT, multiline=True
            )
        ),
        schema.Optional(
            CONF_XSRF_TOKEN, default="", description=description
        ): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }


def _scan_interval_field() -> dict[Any, Any]:
    return {
        schema.Optional(
            CONF_SCAN_INTERVAL,
            default=DEFAULT_SCAN_INTERVAL,
            description={"advanced": True},
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, min=MIN_SCAN_INTERVAL
            )
        )
    }


def _totp_schema() -> Any:
    """The 2FA form: the Base32 secret, or the otpauth:// link, from the QR code.

    The integration keeps it and generates a fresh code on every sign-in, so the
    user never types a code.
    """

    return schema.Schema(
        {
            schema.Required(CONF_TOTP_SECRET): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )
        }
    )


async def _async_authenticate(
    client: MozillionClient, data: dict[str, Any]
) -> dict[str, Any]:
    """Resolve the session to use, logging in when credentials are supplied."""

    email = data.get(CONF_EMAIL)
    password = data.get(CONF_PASSWORD)

    if email and password:
        # Deliberately no email, following api.py: a debug log gets pasted into
        # public bug reports, and the account is already known from the entry.
        _LOGGER.debug("Authenticating with the credentials on the entry")
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


async def _async_try_login(
    client: MozillionClient, data: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Run the login, returning the session or the error key to show."""

    try:
        return await _async_authenticate(client, data), None
    except ValueError:
        return None, "missing_auth"
    except MozillionTwoFactorRequired:
        return None, "two_factor_required"
    except (MozillionTotpSecretError, MozillionTwoFactorRejected):
        return None, "invalid_totp"
    except MozillionSolverError:
        _LOGGER.exception("FlareSolverr could not clear the challenge")
        return None, "solver_failed"
    except MozillionChallengeError:
        return None, "cloudflare_blocked"
    # After the 2FA errors: a rejected password is a wrong password, not an
    # unreachable server.
    except MozillionAuthError:
        _LOGGER.debug("Mozillion rejected the credentials")
        return None, "invalid_auth"
    except RuntimeError:
        _LOGGER.exception("Login failed")
        return None, "cannot_connect"


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


async def _async_session_for_entry(
    client: MozillionClient, entry: ConfigEntry
) -> tuple[str, str | None]:
    """Return working cookies for the account, using the caller's client.

    The stored session is tried first; a stale one is renewed from the entry's
    credentials, the same way the coordinator recovers. The client is passed in
    rather than built here: every step of a flow shares the one session its
    invocation created (``_dedicated_client``).
    """

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
        return await log_in()

    try:
        await client.async_fetch_sims(
            cookie_header=cookie_header, xsrf_token=xsrf_token
        )
    except MozillionAuthError:
        if not has_credentials(entry):
            raise
        _LOGGER.debug("Stored session expired; logging in to list SIMs")
        return await log_in()

    return cookie_header, xsrf_token


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
        # Email, password and the advanced fields, held while the TOTP step runs.
        self._pending: dict[str, Any] = {}
        # Set once Cloudflare blocks a login: the sign-in form then also offers a
        # FlareSolverr address, which is hidden until it is relevant.
        self._show_solver = False
        # Which sign-in form the user picked: "credentials" or "session".
        self._mode = "credentials"
        self._sims: list[MozillionSim] = []
        self._cookie_header: str | None = None
        self._xsrf_token: str | None = None
        self._reconfigure_entry: ConfigEntry | None = None
        # One client (and therefore one aiohttp session) for this invocation, so
        # retrying a step -- a mistyped password, an unreadable dashboard -- does
        # not leave another connector behind.
        self._client: MozillionClient | None = None

    def _async_client(self) -> MozillionClient:
        """Return this flow's client, building its session on first use."""

        if self._client is None:
            self._client = _dedicated_client(self.hass)
        return self._client

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
        """Choose how to sign in: email and password, or a browser session."""

        return self.async_show_menu(
            step_id="user", menu_options=["credentials", "session"]
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Sign in with email and password.

        2FA is not asked up front: it is requested only if Mozillion's login
        actually answers with a 2FA challenge (``async_step_totp``).
        """

        self._mode = "credentials"
        if user_input is not None:
            self._pending = dict(user_input)
            return await self._async_login("user", self._pending)
        return self._main_form("user", {})

    async def async_step_session(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Sign in with the cookie header and XSRF token from a browser."""

        self._mode = "session"
        if user_input is not None:
            self._pending = dict(user_input)
            return await self._async_login("user", self._pending)
        return self._main_form("user", {})

    async def async_step_totp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect the 2FA secret once Mozillion has asked for it."""

        return await self._async_totp_step("user", user_input)

    # -- shared by setup, reauth and reconfigure ---------------------------

    def _entry_for(self, kind: str) -> ConfigEntry:
        if kind == "reauth":
            return self._get_reauth_entry()
        return self._reconfigure_entry or self._get_reconfigure_entry()

    async def _async_totp_step(
        self, kind: str, user_input: dict[str, Any] | None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return await self._async_login(
                kind, {**self._pending, **user_input}, from_totp=True
            )
        return self._totp_form(kind, {})

    def _main_form(
        self, kind: str, errors: dict[str, str]
    ) -> config_entries.ConfigFlowResult:
        if kind == "user":
            if self._mode == "session":
                step_id = "session"
                fields = _session_fields(required=True, advanced=False)
            else:
                step_id = "credentials"
                fields = _login_fields(self._pending, required=True)
                if self._show_solver:
                    fields.update(
                        _solver_field(self._pending.get(CONF_FLARESOLVERR_URL, ""))
                    )
            return self.async_show_form(
                step_id=step_id,
                data_schema=schema.Schema({**fields, **_scan_interval_field()}),
                errors=errors,
            )

        entry = self._entry_for(kind)
        stored = str(entry.data.get(CONF_FLARESOLVERR_URL) or "")
        return self.async_show_form(
            step_id=f"{kind}_confirm",
            data_schema=schema.Schema(
                {
                    **_login_fields(entry.data, required=False),
                    **(_solver_field(stored) if self._show_solver or stored else {}),
                    **_session_fields(required=False, advanced=True),
                }
            ),
            errors=errors,
        )

    def _totp_form(
        self, kind: str, errors: dict[str, str]
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_form(
            step_id="totp" if kind == "user" else f"{kind}_totp",
            data_schema=_totp_schema(),
            errors=errors,
        )

    async def _async_try_login(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Run the login, returning the session or the error key to show."""

        return await _async_try_login(self._async_client(), data)

    async def _async_login(
        self, kind: str, data: dict[str, Any], *, from_totp: bool = False
    ) -> config_entries.ConfigFlowResult:
        """Log in, ask for the 2FA secret only if needed, then finish the flow."""

        data = dict(data)
        if kind != "user":
            # Reuse what is stored so a routine reauth never asks for it again.
            stored = self._entry_for(kind).data
            data.setdefault(CONF_TOTP_SECRET, stored.get(CONF_TOTP_SECRET, ""))
            data.setdefault(
                CONF_FLARESOLVERR_URL, stored.get(CONF_FLARESOLVERR_URL, "")
            )
        solver = str(data.get(CONF_FLARESOLVERR_URL) or "").strip()
        data[CONF_FLARESOLVERR_URL] = solver
        self._async_client().flaresolverr_url = solver or None

        result, error = await self._async_try_login(data)
        if error in _TOTP_ERRORS:
            return self._totp_form(
                kind, {} if error == "two_factor_required" else {"base": error}
            )
        if error in _BLOCKED_ERRORS:
            # Not a credential problem, so go back to the form that can fix it.
            self._show_solver = True
            return self._main_form(kind, {"base": error})
        if error or result is None:
            errors = {"base": error or "cannot_connect"}
            if from_totp:
                return self._totp_form(kind, errors)
            return self._main_form(kind, errors)

        if kind == "user":
            return await self._async_continue_setup(result)
        return await self._async_save_entry(kind, result)

    async def _async_continue_setup(
        self, data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """The account is authenticated: find its SIMs."""

        # One entry per account: further SIMs are added to this one.
        email = str(data.get(CONF_EMAIL) or "").strip().lower()
        if email:
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

        self._credentials = data
        self._cookie_header = data.get(CONF_SESSION_COOKIE)
        self._xsrf_token = data.get(CONF_XSRF_TOKEN)

        try:
            self._sims = await self._async_client().async_fetch_sims(
                cookie_header=self._cookie_header or "",
                xsrf_token=self._xsrf_token,
            )
        except MozillionChallengeError:
            self._show_solver = self._mode == "credentials"
            return self._main_form("user", {"base": "cloudflare_blocked"})
        except RuntimeError:
            _LOGGER.exception(
                "Failed to fetch the SIM list; falling back to manual entry"
            )
            self._sims = []

        if self._sims:
            return await self.async_step_select_sim()
        return await self.async_step_manual_ids()

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
            if sim is not None and await self._async_validate_sim(
                self._async_client(),
                self._cookie_header or "",
                self._xsrf_token,
                sim,
            ):
                return self._create_account(sim)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="select_sim",
            data_schema=schema.Schema(
                {schema.Required("sim"): schema.In(list(choices))}
            ),
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
            if await self._async_validate_sim(
                self._async_client(),
                self._cookie_header or "",
                self._xsrf_token,
                sim,
            ):
                return self._create_account(sim)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual_ids",
            data_schema=schema.Schema(
                {
                    schema.Required(CONF_ORDER_DETAIL_ID): str,
                    schema.Required(CONF_SIM_META_ID): str,
                    schema.Optional(CONF_SIM_NUMBER, default=""): str,
                }
            ),
            errors=errors,
        )

    async def _async_validate_sim(
        self,
        client: MozillionClient,
        cookie_header: str,
        xsrf_token: str | None,
        sim: MozillionSim,
    ) -> bool:
        """Check this session can actually read the SIM before saving it."""

        try:
            await _async_validate(client, cookie_header, xsrf_token, sim)
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

        if user_input is not None:
            self._pending = dict(user_input)
            return await self._async_login("reauth", self._pending)
        return self._main_form("reauth", {})

    async def async_step_reauth_totp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect a new 2FA secret when the stored one no longer works."""

        return await self._async_totp_step("reauth", user_input)

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

        if user_input is not None:
            self._pending = dict(user_input)
            return await self._async_login("reconfigure", self._pending)
        return self._main_form("reconfigure", {})

    async def async_step_reconfigure_totp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect a new 2FA secret when the stored one no longer works."""

        return await self._async_totp_step("reconfigure", user_input)

    async def _async_save_entry(
        self, kind: str, updated: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Validate the new session against a SIM, then update the entry."""

        entry = self._entry_for(kind)
        new_data = {
            **entry.data,
            CONF_EMAIL: updated.get(CONF_EMAIL) or entry.data.get(CONF_EMAIL, ""),
            CONF_PASSWORD: updated.get(CONF_PASSWORD)
            or entry.data.get(CONF_PASSWORD, ""),
            CONF_TOTP_SECRET: updated.get(CONF_TOTP_SECRET)
            or entry.data.get(CONF_TOTP_SECRET, ""),
            CONF_ORIGIN: updated.get(CONF_ORIGIN)
            or entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
            CONF_FLARESOLVERR_URL: updated.get(CONF_FLARESOLVERR_URL, ""),
            CONF_SESSION_COOKIE: updated.get(CONF_SESSION_COOKIE, ""),
            CONF_XSRF_TOKEN: updated.get(CONF_XSRF_TOKEN, ""),
        }
        if not await self._async_validate_entry(self._async_client(), entry, new_data):
            return self._main_form(kind, {"base": "cannot_connect"})

        if kind == "reauth":
            return self.async_update_reload_and_abort(
                entry, data=new_data, reason="reauth_successful"
            )
        if entry.version != self.VERSION:
            self.hass.config_entries.async_update_entry(entry, version=self.VERSION)
        return self.async_update_reload_and_abort(
            entry, data=new_data, title=_account_title(new_data)
        )

    async def _async_validate_entry(
        self,
        client: MozillionClient,
        entry: ConfigEntry,
        data: dict[str, Any],
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
                client,
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

        self._pending = dict(user_input)
        self._mode = "credentials" if user_input.get(CONF_EMAIL) else "session"
        return await self._async_login("user", self._pending)

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

    # One client per flow invocation, created on first use by `_async_client`.
    _client: MozillionClient | None = None

    def _async_client(self) -> MozillionClient:
        """Return this flow's client, building its session on first use."""

        if self._client is None:
            self._client = _dedicated_client(
                self.hass, self._get_entry().data.get(CONF_FLARESOLVERR_URL)
            )
        return self._client

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
            client = self._async_client()
            try:
                cookie_header, xsrf_token = await _async_session_for_entry(
                    client, self._get_entry()
                )
                saved = await self._async_finish(sim, client, cookie_header, xsrf_token)
            except RuntimeError:
                _LOGGER.exception("Could not read the Mozillion session")
                saved = False
            if saved:
                return self._result_for(sim, self._reconfiguring)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual_ids",
            data_schema=schema.Schema(
                {
                    schema.Required(CONF_ORDER_DETAIL_ID): str,
                    schema.Required(CONF_SIM_META_ID): str,
                    schema.Optional(CONF_SIM_NUMBER, default=""): str,
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

        # One client for the whole invocation: the SIM list read and the later
        # validation share it, so an add-SIM attempt opens one session, not three.
        client = self._async_client()
        try:
            cookie_header, xsrf_token = await _async_session_for_entry(client, entry)
            sims = await client.async_fetch_sims(
                cookie_header=cookie_header, xsrf_token=xsrf_token
            )
        except RuntimeError:
            _LOGGER.exception("Could not read the Mozillion SIM list")
            return await self.async_step_manual_ids()

        # Only a reconfigure flow has a subentry to exclude. Reading
        # `_reconfigure_subentry_id` unconditionally raised
        #   ValueError: Source is user, expected reconfigure
        # on the add-a-SIM path, so an account with any SIM could never gain
        # another.
        reconfigure_id = self._reconfigure_subentry_id if reconfigure else None
        tracked = {
            subentry.data.get(CONF_SIM_META_ID)
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_SIM
            and subentry.subentry_id != reconfigure_id
        }
        choices = _sim_choices(sims, taken={t for t in tracked if t})
        if not choices:
            return self.async_abort(reason="no_new_sims")

        errors: dict[str, str] = {}
        if user_input is not None:
            sim = choices.get(user_input["sim"])
            if sim is None:
                errors["base"] = "cannot_connect"
            elif await self._async_finish(sim, client, cookie_header, xsrf_token):
                return self._result_for(sim, reconfigure)
            else:
                errors["base"] = "cannot_connect"

        # The step id decides where HA routes the submitted form, so it has to
        # match the step that was actually entered. Reporting "user" from the
        # reconfigure path sent the submission to async_step_user, which finished
        # by calling async_create_entry -- and HA rejects that for a reconfigure
        # flow with "ValueError: Source is reconfigure, expected user".
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=schema.Schema(
                {schema.Required("sim"): schema.In(list(choices))}
            ),
            errors=errors,
        )

    async def _async_finish(
        self,
        sim: MozillionSim,
        client: MozillionClient,
        cookie_header: str,
        xsrf_token: str | None,
    ) -> bool:
        """Validate the SIM against the API before saving it.

        The client and cookies come from the step that started the flow: an
        add-SIM submission must not open a second session just to re-check the
        credentials it has already read.
        """

        try:
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
    """Handle Mozillion options.

    The scan interval is account-wide. For an account that signs in with a
    password, the 2FA secret and the FlareSolverr address can be changed here too,
    without going back through reconfigure. A change is only saved once a login
    with the new values has worked.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the polling interval, the 2FA secret and the solver address."""

        entry = self.config_entry
        can_login = has_credentials(entry)
        stored_secret = str(entry.data.get(CONF_TOTP_SECRET) or "")
        stored_solver = str(entry.data.get(CONF_FLARESOLVERR_URL) or "")
        errors: dict[str, str] = {}

        if user_input is not None:
            # A cleared optional field is omitted from the submission, and the
            # form always pre-fills what is stored, so absent means "cleared".
            secret = str(user_input.get(CONF_TOTP_SECRET) or "").strip()
            solver = str(user_input.get(CONF_FLARESOLVERR_URL) or "").strip()

            if can_login and (secret, solver) != (stored_secret, stored_solver):
                client = _dedicated_client(self.hass, solver or None)
                result, error = await _async_try_login(
                    client,
                    {
                        CONF_EMAIL: entry.data[CONF_EMAIL],
                        CONF_PASSWORD: entry.data[CONF_PASSWORD],
                        CONF_TOTP_SECRET: secret,
                        CONF_ORIGIN: entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
                    },
                )
                if error or result is None:
                    errors["base"] = error or "cannot_connect"
                else:
                    # The login that proved the values also produced a session.
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_TOTP_SECRET: secret,
                            CONF_FLARESOLVERR_URL: solver,
                            CONF_SESSION_COOKIE: result[CONF_SESSION_COOKIE],
                            CONF_XSRF_TOKEN: result.get(CONF_XSRF_TOKEN, ""),
                        },
                    )

            if not errors:
                return self.async_create_entry(
                    title="Options",
                    data={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]},
                )
            # Show what the user typed, not what is stored, so a retry keeps it.
            stored_secret, stored_solver = secret, solver

        current = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        fields: dict[Any, Any] = {
            # Same floor as the setup form's NumberSelector: below a minute
            # the poller exhausts Mozillion's request budget and every poll
            # comes back rate-limited. The bound belongs to the form schema
            # so a sub-minute value is refused before it can be saved --
            # Home Assistant answers the dialog with this field's error and
            # keeps the flow open instead of storing it.
            schema.Optional(CONF_SCAN_INTERVAL, default=current): schema.All(
                schema.Coerce(int), schema.Range(min=MIN_SCAN_INTERVAL)
            )
        }
        if can_login:
            fields[
                schema.Optional(CONF_TOTP_SECRET, description=_suggest(stored_secret))
            ] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )
            fields.update(_solver_field(stored_solver))

        return self.async_show_form(
            step_id="init", data_schema=schema.Schema(fields), errors=errors
        )
