"""Data update coordinator for Mozillion polling."""

from __future__ import annotations

import logging
import time
from collections.abc import Coroutine
from datetime import timedelta
from typing import Any, TypeVar

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MozillionAuthError, MozillionClient, MozillionSim, parse_reset_date
from .const import (
    ATTR_DAYS_LEFT,
    ATTR_ICCID,
    ATTR_OVERSPEND_LIMIT_REACHED,
    ATTR_PLAN_DURATION,
    ATTR_PLAN_IS_DATA_ONLY,
    ATTR_PLAN_ROAMING,
    ATTR_PLAN_TARIFF,
    ATTR_PLAN_TEXTS,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_RESET_DATE,
    ATTR_RESET_LABEL,
    ATTR_SIM_NUMBER,
    ATTR_SIM_STATUS,
    ATTR_TOTAL,
    ATTR_TOTAL_GBR,
    ATTR_TOTAL_GLOBAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_GBR,
    ATTR_USAGE_GLOBAL,
    ATTR_USAGE_PERCENTAGE,
    ATTR_WALLET,
    ATTR_WALLET_BALANCE,
    ATTR_WALLET_SPEND,
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_XSRF_TOKEN,
    DASHBOARD_REFRESH_INTERVAL,
    DOMAIN,
    ISSUE_DASHBOARD_UNREADABLE,
    REPAIR_FAILURE_THRESHOLD,
)
from .session import MozillionSession, has_credentials

_LOGGER = logging.getLogger(__name__)

CoordinatorData = dict[str, Any]

_T = TypeVar("_T")


def _to_float(value: Any) -> float | None:
    """Coerce an API value to float, treating blanks/None as unknown."""

    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MozillionCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator to poll the Mozillion usage endpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MozillionClient,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        session: MozillionSession,
        update_interval: timedelta,
    ) -> None:
        self.client = client
        self.subentry = subentry
        # One session per account, shared with every other SIM's coordinator.
        self.session = session
        self._reset_poll_state()

        super().__init__(
            hass,
            _LOGGER,
            name=f"Mozillion Data {subentry.title}",
            update_interval=update_interval,
            # async_config_entry_first_refresh refuses to run for a coordinator
            # that does not know which config entry owns it.
            config_entry=entry,
        )

    def _reset_poll_state(self) -> None:
        """Initialise the per-session poll state.

        Kept out of ``__init__`` so tests that build a coordinator without running
        it can reach the same state by calling this, instead of mirroring each
        attribute by hand and silently breaking when one is added.
        """

        # Consecutive failed reads of the dashboard, used to decide when the
        # markup breakage is worth telling the user about, and whether we have an
        # outstanding repair to withdraw.
        self._dashboard_failures = 0
        self._dashboard_repair_active = False
        # Last good dashboard reading, reused until DASHBOARD_REFRESH_INTERVAL
        # has passed, so the big page is not re-read every poll.
        self._sim_detail: MozillionSim | None = None
        self._sim_detail_at: float | None = None

    async def _async_refresh_auth(self) -> None:
        """Log in through the shared session, persisting it only once.

        The session serialises logins, so whichever SIM gets there first does the
        work and the rest reuse the cookies; only that first one writes the entry.
        """

        if await self.session.async_authenticate(self.client, self.config_entry):
            await self._async_persist_auth()

    async def _async_persist_auth(self) -> None:
        """Persist the refreshed session back to the config entry.

        This keeps the stored ``session_cookie``/``xsrf_token`` current so a
        reload or restart does not immediately start with a stale token.
        """

        if self.config_entry is None:
            return
        new_data = dict(self.config_entry.data)
        new_data[CONF_SESSION_COOKIE] = self.session.cookie_header or ""
        new_data[CONF_XSRF_TOKEN] = self.session.xsrf_token or ""
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)

    async def _async_fetch_usage(self) -> dict[str, Any]:
        """Call the usage endpoints for this SIM with the shared session."""

        return await self.client.async_get_usage(
            order_detail_id=self.subentry.data[CONF_ORDER_DETAIL_ID],
            sim_meta_id=self.subentry.data[CONF_SIM_META_ID],
            cookie_header=self.session.cookie_header or "",
            xsrf_token=self.session.xsrf_token,
        )

    async def _async_fetch_sim(self) -> MozillionSim:
        """Read the plan/reset/status detail for this SIM from the dashboard."""

        return await self.client.async_fetch_sim(
            sim_meta_id=self.subentry.data[CONF_SIM_META_ID],
            cookie_header=self.session.cookie_header or "",
            xsrf_token=self.session.xsrf_token,
        )

    async def _async_fetch_wallet(self) -> dict[str, Any]:
        """Read the out-of-bundle wallet position for this order."""

        return await self.client.async_fetch_overspend(
            order_detail_id=self.subentry.data[CONF_ORDER_DETAIL_ID],
            cookie_header=self.session.cookie_header or "",
            xsrf_token=self.session.xsrf_token,
        )

    async def _async_read_optional(
        self, what: str, coro: Coroutine[Any, Any, _T]
    ) -> _T | None:
        """Read something the integration can live without.

        Usage is this integration's job; the plan blurb and the wallet position
        are extras. If Mozillion changes that markup we would rather lose the
        extras than lose data usage entirely, so a non-auth failure here is
        logged and skipped. An expired session still propagates, because that
        needs the reauth flow rather than a silent downgrade.
        """

        try:
            return await coro
        except MozillionAuthError:
            raise
        except (RuntimeError, ClientError) as err:
            _LOGGER.warning("Could not read %s from Mozillion: %s", what, err)
            return None

    async def _async_read_all(
        self,
    ) -> tuple[dict[str, Any], MozillionSim | None, dict[str, Any] | None]:
        """Read usage, then the extras that are allowed to fail."""

        raw = await self._async_fetch_usage()
        sim = await self._async_read_dashboard()
        wallet = await self._async_read_optional(
            "the wallet balance", self._async_fetch_wallet()
        )
        return raw, sim, wallet

    async def _async_read_dashboard(self) -> MozillionSim | None:
        """Read the dashboard detail, tracking whether the page still parses.

        The 355 KB dashboard page carries the plan, service status and reset date --
        none of which change on an hourly basis (the reset label is monthly) -- so it
        is refreshed on its own slower cadence and the rest of the poll reuses it.
        Usage and the wallet come from small JSON calls and keep the full interval.

        The page markup is also this integration's one genuinely fragile dependency,
        and when it changes the only symptom used to be a log line: the plan, status
        and reset entities quietly went unavailable and the user had no idea why.
        Consecutive failures now raise a repair, because the fix is outside Home
        Assistant, and the last good reading keeps being served meanwhile.
        """

        cached = self._cached_sim_detail()
        if cached is not None:
            return cached

        try:
            sim = await self._async_fetch_sim()
        except MozillionAuthError:
            raise
        except (RuntimeError, ClientError) as err:
            self._dashboard_failures += 1
            _LOGGER.warning(
                "Could not read the Mozillion dashboard (%d consecutive "
                "failure(s)): %s",
                self._dashboard_failures,
                err,
            )
            if self._dashboard_failures >= REPAIR_FAILURE_THRESHOLD:
                self._raise_dashboard_repair(str(err))
            # Keep serving the last good reading rather than blanking the plan,
            # status and reset entities over one failed refresh.
            return self._sim_detail

        self._sim_detail = sim
        self._sim_detail_at = time.monotonic()

        if self._dashboard_failures:
            _LOGGER.info(
                "Mozillion dashboard read recovered after %d failure(s)",
                self._dashboard_failures,
            )
            self._dashboard_failures = 0
        self._clear_dashboard_repair()
        return sim

    def _cached_sim_detail(self) -> MozillionSim | None:
        """Return the cached dashboard detail while it is still fresh."""

        if self._sim_detail is None or self._sim_detail_at is None:
            return None
        if (time.monotonic() - self._sim_detail_at) < DASHBOARD_REFRESH_INTERVAL:
            return self._sim_detail
        return None

    def _raise_dashboard_repair(self, error: str) -> None:
        """Tell the user the dashboard cannot be read."""

        # Re-created on each failing poll so the reported attempt count and last
        # error stay current; the registry keys on the issue id, so this updates
        # the existing issue rather than stacking new ones.
        self._dashboard_repair_active = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_DASHBOARD_UNREADABLE,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_DASHBOARD_UNREADABLE,
            translation_placeholders={
                "attempts": str(self._dashboard_failures),
                "error": error[:200],
                "entry": self.subentry.title,
            },
        )

    def _clear_dashboard_repair(self) -> None:
        """Withdraw the repair, but only if one was raised.

        Guarded so an ordinary healthy poll never touches the issue registry --
        only a recovery transition does.
        """

        if not self._dashboard_repair_active:
            return
        self._dashboard_repair_active = False
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_DASHBOARD_UNREADABLE)

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch data from API, transparently re-authenticating on expiry."""

        _LOGGER.debug("Update cycle started")
        try:
            if self.session.needs_authentication(self.config_entry):
                await self._async_refresh_auth()

            if not self.session.cookie_header:
                raise ConfigEntryAuthFailed(
                    "No session cookie and no credentials are configured to "
                    "obtain one. Please reconfigure the integration."
                )

            raw, sim, wallet = await self._async_read_all()
        except MozillionAuthError as err:
            raw, sim, wallet = await self._async_retry_after_auth_error(err)
        except (RuntimeError, ClientError) as err:
            _LOGGER.error("Update failed: %s", err)
            raise UpdateFailed(err) from err

        data = _build_coordinator_data(raw, sim=sim, wallet=wallet)
        data[ATTR_SIM_NUMBER] = self.subentry.data.get(CONF_SIM_NUMBER, "")
        data[ATTR_ICCID] = self.subentry.data.get(CONF_ICCID, "")

        _LOGGER.debug(
            "Update success for %s: usage=%s, total=%s, remaining=%s, "
            "percentage=%s, unlimited=%s, status=%s, reset=%s, wallet=%s",
            self.subentry.title,
            data[ATTR_USAGE],
            data[ATTR_TOTAL],
            data[ATTR_REMAINING],
            data[ATTR_USAGE_PERCENTAGE],
            data[ATTR_UNLIMITED],
            data[ATTR_SIM_STATUS],
            data[ATTR_RESET_DATE],
            data[ATTR_WALLET],
        )
        return data

    async def _async_retry_after_auth_error(
        self, error: MozillionAuthError
    ) -> tuple[dict[str, Any], MozillionSim | None, dict[str, Any] | None]:
        """Re-authenticate once after a rejected session, then retry the fetch.

        Only entries with credentials can recover; a cookie-only entry has to
        hand the problem to the user via a reauth flow.
        """

        if not has_credentials(self.config_entry):
            raise ConfigEntryAuthFailed(
                "Session expired and no credentials are configured to "
                "re-authenticate. Please update the integration's credentials or "
                "provide a fresh cookie."
            ) from error

        _LOGGER.warning("Mozillion session expired; re-authenticating and retrying")
        try:
            # Force the shared session to log in again: the cookie it holds was
            # just rejected, so waiting for the age threshold would keep failing.
            self.session.authenticated_at = None
            await self._async_refresh_auth()
            if not self.session.cookie_header:
                raise UpdateFailed(
                    "Re-authentication failed to obtain a session"
                ) from None
            return await self._async_read_all()
        except MozillionAuthError as retry_error:
            # The credentials themselves are wrong: a retry will not help, so
            # ask the user instead of looping.
            raise ConfigEntryAuthFailed(
                "Mozillion rejected the configured credentials"
            ) from retry_error
        except (RuntimeError, ClientError) as retry_error:
            raise UpdateFailed(retry_error) from retry_error


def _build_coordinator_data(
    raw: dict[str, Any],
    sim: MozillionSim | None = None,
    wallet: dict[str, Any] | None = None,
) -> CoordinatorData:
    """Turn the Mozillion payloads into coordinator data.

    ``usedData``/``totalData`` are what the dashboard itself renders, so they
    drive the primary sensors. The GBR and global buckets are carried through
    as diagnostics -- they are unverified (see the integration docs). ``sim``
    and ``wallet`` are optional: the integration keeps working without them.
    """

    usage = _to_float(raw.get("usedData"))
    total = _to_float(raw.get("totalData"))
    unlimited = bool(raw.get("isUnlimited"))

    remaining: float | None = None
    usage_percentage: float | None = None

    if usage is not None and total is not None and total > 0:
        # Mirror the dashboard: never show a negative balance or >100% used.
        remaining = max(0.0, total - usage)
        usage_percentage = min(100.0, (usage / total) * 100)

    data: CoordinatorData = {
        ATTR_RAW: raw,
        ATTR_USAGE: usage,
        ATTR_TOTAL: total,
        ATTR_REMAINING: remaining,
        ATTR_USAGE_PERCENTAGE: usage_percentage,
        ATTR_UNLIMITED: unlimited,
        ATTR_USAGE_GBR: _to_float(raw.get("usedDataGbr")),
        ATTR_TOTAL_GBR: _to_float(raw.get("totalDataGbr")),
        ATTR_USAGE_GLOBAL: _to_float(raw.get("usedDataGlobal")),
        ATTR_TOTAL_GLOBAL: _to_float(raw.get("totalDataGlobal")),
        ATTR_SIM_STATUS: "",
        ATTR_RESET_DATE: None,
        ATTR_RESET_LABEL: "",
        ATTR_DAYS_LEFT: "",
        ATTR_PLAN_TARIFF: "",
        ATTR_PLAN_DURATION: "",
        ATTR_PLAN_ROAMING: "",
        ATTR_PLAN_TEXTS: "",
        ATTR_PLAN_IS_DATA_ONLY: False,
        ATTR_WALLET: None,
        ATTR_WALLET_BALANCE: None,
        ATTR_WALLET_SPEND: None,
        ATTR_OVERSPEND_LIMIT_REACHED: None,
    }

    if sim is not None:
        data[ATTR_SIM_STATUS] = sim.status
        data[ATTR_RESET_LABEL] = sim.reset_label
        data[ATTR_DAYS_LEFT] = sim.days_left
        data[ATTR_RESET_DATE] = parse_reset_date(sim.reset_label, sim.days_left)
        data[ATTR_PLAN_TARIFF] = sim.plan_data_tariff
        data[ATTR_PLAN_DURATION] = sim.plan_duration
        data[ATTR_PLAN_ROAMING] = sim.plan_roaming
        data[ATTR_PLAN_TEXTS] = sim.plan_texts_minutes
        data[ATTR_PLAN_IS_DATA_ONLY] = sim.plan_is_data_only

    if wallet is not None:
        data[ATTR_WALLET] = wallet
        # `remaining` is the figure mozillion.com displays as "Your balance";
        # `balance` is carried through untouched inside ATTR_WALLET.
        data[ATTR_WALLET_BALANCE] = _to_float(wallet.get("remaining"))
        data[ATTR_WALLET_SPEND] = _to_float(wallet.get("spent"))
        data[ATTR_OVERSPEND_LIMIT_REACHED] = bool(wallet.get("reached"))

    return data


def wallet_is_active(data: CoordinatorData) -> bool:
    """Return True when the out-of-bundle wallet is worth reporting.

    Mozillion hides the wallet behind a "top up to start using these features"
    prompt until the SIM has top-up history or a positive balance, and a
    never-topped-up wallet answers all zeros with ``reached: true`` -- which
    would read as "you have hit your limit" when the truth is "you have no
    wallet". The entities use this to report unavailable instead.
    """

    if data.get(ATTR_WALLET) is None:
        return False
    balance = data.get(ATTR_WALLET_BALANCE)
    spend = data.get(ATTR_WALLET_SPEND)
    return bool(
        (balance is not None and balance > 0) or (spend is not None and spend > 0)
    )
