"""Data update coordinator for Mozillion polling."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from datetime import timedelta
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    MozillionAuthError,
    MozillionClient,
    MozillionSim,
    mask_identifier,
    parse_reset_date,
    to_float,
)
from .const import (
    ATTR_BILLING_AMOUNT,
    ATTR_BILLING_DAYS,
    ATTR_DAYS_LEFT,
    ATTR_HAS_BILL,
    ATTR_OVERSPEND_LIMIT_REACHED,
    ATTR_PLAN_DURATION,
    ATTR_PLAN_IS_DATA_ONLY,
    ATTR_PLAN_PARENTAL_CONTROL,
    ATTR_PLAN_ROAMING,
    ATTR_PLAN_TARIFF,
    ATTR_PLAN_TEXTS,
    ATTR_PLAN_VOICEMAIL,
    ATTR_PORT_DATE,
    ATTR_PORT_STATUS,
    ATTR_PORT_STATUS_DESCRIPTION,
    ATTR_PORT_STATUS_LABEL,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_RESET_DATE,
    ATTR_RESET_LABEL,
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
    CONF_ORDER_DETAIL_ID,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_XSRF_TOKEN,
    DASHBOARD_REFRESH_INTERVAL,
    DOMAIN,
    ISSUE_DASHBOARD_UNREADABLE,
    REPAIR_FAILURE_THRESHOLD,
    SUBENTRY_TYPE_SIM,
)
from .session import MozillionSession, has_credentials

_LOGGER = logging.getLogger(__name__)

CoordinatorData = dict[str, Any]

_T = TypeVar("_T")


def _coordinator_label(subentry: ConfigSubentry) -> str:
    """Name a coordinator without putting the phone number in the logs."""

    sim_number = str(subentry.data.get(CONF_SIM_NUMBER) or "")
    return mask_identifier(sim_number) if sim_number else SUBENTRY_TYPE_SIM


class MozillionDashboard:
    """Every SIM's dashboard detail for one account, read once per window.

    ``MozillionClient.async_fetch_sims`` downloads and parses the whole ~355 KB
    dashboard page, and that one page lists *every* SIM on the account. Holding the
    cache on each coordinator therefore downloaded the same page once per SIM per
    window -- N times the bandwidth and N spends from Mozillion's request budget on
    an account with N SIMs -- which is issue #73.

    So one of these is built per config entry, in ``async_setup_entry``, and shared
    by every SIM's coordinator the way :class:`MozillionSession` is. It owns the
    three things that make the read account-wide rather than per-SIM: the freshness
    window (:data:`DASHBOARD_REFRESH_INTERVAL`), the parsed SIMs keyed by
    ``sim_meta_id``, and the lock that makes two coordinators polling in the same
    cycle perform one fetch.

    Taking the lock and *then* re-checking freshness is :class:`MozillionSession`'s
    shape, and for the same reason: the loser of the race has to find the winner's
    result already in place, or the lock would only serialise two duplicate reads
    instead of preventing one.
    """

    def __init__(self, client: MozillionClient, session: MozillionSession) -> None:
        self._client = client
        self._session = session
        # The last successful read, keyed by sim_meta_id. Never holds a failure.
        self._sims: dict[str, MozillionSim] = {}
        # Monotonic time of that read, or None before the first success.
        self._fetched_at: float | None = None
        # Only one fetch may be in flight, however many SIMs poll at once.
        self._lock: asyncio.Lock = asyncio.Lock()

    def cached(self, sim_meta_id: str) -> MozillionSim | None:
        """Return this SIM's detail while the account's last read is still fresh."""

        if not self._is_fresh():
            return None
        return self._sims.get(sim_meta_id)

    def last_known(self, sim_meta_id: str) -> MozillionSim | None:
        """Return this SIM's most recent detail, however old it is.

        Only the fallback for a failed refresh: one bad read must not blank the
        plan, status and reset entities of a SIM that was read fine before.
        """

        return self._sims.get(sim_meta_id)

    async def async_get(self, sim_meta_id: str) -> MozillionSim:
        """Return one SIM's detail, reading the account's page at most once."""

        sim = self.cached(sim_meta_id)
        if sim is not None:
            return sim

        async with self._lock:
            # Another SIM's coordinator may have read the page while we waited for
            # the lock, which is the entire point of taking it.
            if self._is_fresh():
                return self._require(sim_meta_id)

            sims = await self._client.async_fetch_sims(
                cookie_header=self._session.cookie_header or "",
                xsrf_token=self._session.xsrf_token,
            )
            # Successes only. A raise above leaves the previous reading and its
            # timestamp untouched, so the next mid-cycle caller retries instead of
            # being served a cached error, and the SIMs that did read keep their
            # last good detail.
            self._sims = {detail.sim_meta_id: detail for detail in sims}
            self._fetched_at = time.monotonic()
            return self._require(sim_meta_id)

    def _is_fresh(self) -> bool:
        """Return True while the last successful read is inside the window."""

        if self._fetched_at is None:
            return False
        return (time.monotonic() - self._fetched_at) < DASHBOARD_REFRESH_INTERVAL

    def _require(self, sim_meta_id: str) -> MozillionSim:
        """Return a SIM the freshly read page must list, or explain the absence."""

        sim = self._sims.get(sim_meta_id)
        if sim is None:
            raise RuntimeError(
                f"SIM {sim_meta_id} is no longer listed on the Mozillion "
                "dashboard; it may have been removed from the account"
            )
        return sim


class MozillionCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator to poll the Mozillion usage endpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MozillionClient,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        session: MozillionSession,
        dashboard: MozillionDashboard,
        update_interval: timedelta,
    ) -> None:
        self.client = client
        self.subentry = subentry
        # One session per account, shared with every other SIM's coordinator.
        self.session = session
        # ...and one dashboard read per account too: the page carries every SIM,
        # so sharing it turns N downloads per window into one.
        self.dashboard = dashboard
        self._reset_poll_state()

        super().__init__(
            hass,
            _LOGGER,
            # The subentry title is the dashboard's display name, which carries
            # the phone number, and this name lands in Home Assistant's own log
            # lines ("Finished fetching ... data"). Only a masked number is used.
            name=f"Mozillion Data {_coordinator_label(subentry)}",
            update_interval=update_interval,
            # async_config_entry_first_refresh refuses to run for a coordinator
            # that does not know which config entry owns it.
            config_entry=entry,
        )

    @property
    def entry(self) -> ConfigEntry:
        """The config entry, narrowed from HA's optional attribute.

        ``DataUpdateCoordinator.config_entry`` is typed ``ConfigEntry | None``
        because a coordinator may be built without one. This one is always given
        one by ``__init__``, so every use site can rely on it. Deliberately a
        property rather than a second attribute: one source of truth means the
        two can never disagree.
        """

        assert self.config_entry is not None
        return self.config_entry

    def _reset_poll_state(self) -> None:
        """Initialise the per-session poll state.

        Kept out of ``__init__`` so tests that build a coordinator without running
        it can reach the same state by calling this, instead of mirroring each
        attribute by hand and silently breaking when one is added.
        """

        # Consecutive failed reads of the dashboard, used to decide when the
        # markup breakage is worth telling the user about, and whether we have an
        # outstanding repair to withdraw. Per SIM, because each SIM's plan, status
        # and reset entities are what go stale; the parsed page itself and its
        # freshness window belong to the account and live on the shared
        # MozillionDashboard.
        self._dashboard_failures = 0
        self._dashboard_repair_active = False

    async def _async_refresh_auth(self) -> None:
        """Log in through the shared session, persisting it only once.

        The session serialises logins, so whichever SIM gets there first does the
        work and the rest reuse the cookies; only that first one writes the entry.
        """

        if await self.session.async_authenticate(self.client, self.entry):
            await self._async_persist_auth()

    async def _async_persist_auth(self) -> None:
        """Persist the refreshed session back to the config entry.

        This keeps the stored ``session_cookie``/``xsrf_token`` current so a
        reload or restart does not immediately start with a stale token.
        """

        new_data = dict(self.entry.data)
        new_data[CONF_SESSION_COOKIE] = self.session.cookie_header or ""
        new_data[CONF_XSRF_TOKEN] = self.session.xsrf_token or ""
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def _async_fetch_usage(self) -> dict[str, Any]:
        """Call the usage endpoints for this SIM with the shared session."""

        return await self.client.async_get_usage(
            order_detail_id=self.subentry.data[CONF_ORDER_DETAIL_ID],
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
        except RuntimeError as err:
            # RuntimeError, not aiohttp's ClientError: MozillionClient converts
            # every ClientError into a RuntimeError at the API boundary, so one
            # cannot arrive here. MozillionAuthError is a RuntimeError subclass,
            # which is why it is handled first and re-raised.
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
        """Read this SIM's dashboard detail through the account's shared holder.

        The 355 KB dashboard page carries the plan, service status and reset date --
        none of which change on an hourly basis (the reset label is monthly) -- so it
        is refreshed on its own slower cadence and the rest of the poll reuses it.
        Usage and the wallet come from small JSON calls and keep the full interval.

        The page is read once per *account*, not once per SIM: it lists every SIM
        anyway, so :class:`MozillionDashboard` fetches it inside the window and this
        SIM asks only for its own entry. Counting failures stays per SIM, because a
        SIM's own plan, status and reset entities are what go without.

        The page markup is also this integration's one genuinely fragile dependency,
        and when it changes the only symptom used to be a log line: the plan, status
        and reset entities quietly went unavailable and the user had no idea why.
        Consecutive failures now raise a repair, because the fix is outside Home
        Assistant, and the last good reading keeps being served meanwhile.
        """

        sim_meta_id = str(self.subentry.data[CONF_SIM_META_ID])
        cached = self.dashboard.cached(sim_meta_id)
        if cached is not None:
            # Another SIM's coordinator -- or this one on an earlier poll -- read
            # the page inside the window, which is proof that it still parses, so
            # a repair this SIM is still carrying can be withdrawn.
            self._note_dashboard_success()
            return cached

        try:
            sim = await self.dashboard.async_get(sim_meta_id)
        except MozillionAuthError:
            raise
        except RuntimeError as err:
            self._dashboard_failures += 1
            _LOGGER.warning(
                "Could not read the Mozillion dashboard (%d consecutive "
                "failure(s)): %s",
                self._dashboard_failures,
                err,
            )
            if self._dashboard_failures >= REPAIR_FAILURE_THRESHOLD:
                self._raise_dashboard_repair(str(err))
            # Keep serving this SIM's last good reading rather than blanking the
            # plan, status and reset entities over one failed refresh.
            return self.dashboard.last_known(sim_meta_id)

        self._note_dashboard_success()
        return sim

    def _note_dashboard_success(self) -> None:
        """Record a good read: clear the failure streak and any repair.

        Called on a shared cache hit as well as on a fresh read. A cache hit means
        the page was parsed inside the window by *some* SIM on this account, which
        is exactly the condition the repair exists to report the absence of.
        """

        if self._dashboard_failures:
            _LOGGER.info(
                "Mozillion dashboard read recovered after %d failure(s)",
                self._dashboard_failures,
            )
            self._dashboard_failures = 0
        self._clear_dashboard_repair()

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
                "entry": _coordinator_label(self.subentry),
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
            if self.session.needs_authentication(self.entry):
                await self._async_refresh_auth()

            if not self.session.cookie_header:
                raise ConfigEntryAuthFailed(
                    "No session cookie and no credentials are configured to "
                    "obtain one. Please reconfigure the integration."
                )

            raw, sim, wallet = await self._async_read_all()
        except MozillionAuthError as err:
            raw, sim, wallet = await self._async_retry_after_auth_error(err)
        except RuntimeError as err:
            _LOGGER.error("Update failed: %s", err)
            raise UpdateFailed(err) from err

        data = _build_coordinator_data(raw, sim=sim, wallet=wallet)

        _LOGGER.debug(
            "Update success for %s: usage=%s, total=%s, remaining=%s, "
            "percentage=%s, unlimited=%s, status=%s, reset=%s, wallet=%s",
            _coordinator_label(self.subentry),
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

        if not has_credentials(self.entry):
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
        except RuntimeError as retry_error:
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

    usage = to_float(raw.get("usedData"))
    total = to_float(raw.get("totalData"))
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
        ATTR_USAGE_GBR: to_float(raw.get("usedDataGbr")),
        ATTR_TOTAL_GBR: to_float(raw.get("totalDataGbr")),
        ATTR_USAGE_GLOBAL: to_float(raw.get("usedDataGlobal")),
        ATTR_TOTAL_GLOBAL: to_float(raw.get("totalDataGlobal")),
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
        ATTR_PORT_STATUS: "",
        ATTR_PORT_STATUS_LABEL: "",
        ATTR_PORT_STATUS_DESCRIPTION: "",
        ATTR_PORT_DATE: "",
        ATTR_BILLING_AMOUNT: None,
        ATTR_HAS_BILL: False,
        ATTR_BILLING_DAYS: "",
    }

    if sim is not None:
        data[ATTR_SIM_STATUS] = sim.status
        data[ATTR_RESET_LABEL] = sim.reset_label
        data[ATTR_DAYS_LEFT] = sim.days_left
        # Home Assistant's clock, not the process's: ``date.today()`` is the
        # container's time zone, which is usually UTC while the user's Home
        # Assistant is not, so between midnight and the UTC offset the reset
        # date came out a day early.
        data[ATTR_RESET_DATE] = parse_reset_date(
            sim.reset_label, sim.days_left, today=dt_util.now().date()
        )
        data[ATTR_PLAN_TARIFF] = sim.plan_data_tariff
        data[ATTR_PLAN_DURATION] = sim.plan_duration
        data[ATTR_PLAN_ROAMING] = sim.plan_roaming
        data[ATTR_PLAN_TEXTS] = sim.plan_texts_minutes
        data[ATTR_PLAN_IS_DATA_ONLY] = sim.plan_is_data_only
        data[ATTR_PLAN_VOICEMAIL] = sim.plan_voicemail
        data[ATTR_PLAN_PARENTAL_CONTROL] = sim.plan_parental_control
        data[ATTR_PORT_STATUS] = sim.port_status
        data[ATTR_PORT_STATUS_LABEL] = sim.port_status_label
        data[ATTR_PORT_STATUS_DESCRIPTION] = sim.port_status_description
        data[ATTR_PORT_DATE] = sim.port_date
        data[ATTR_BILLING_AMOUNT] = sim.billing_amount
        data[ATTR_HAS_BILL] = sim.has_bill
        data[ATTR_BILLING_DAYS] = sim.billing_days

    if wallet is not None:
        data[ATTR_WALLET] = wallet
        # `remaining` is the figure mozillion.com displays as "Your balance";
        # `balance` is carried through untouched inside ATTR_WALLET.
        data[ATTR_WALLET_BALANCE] = to_float(wallet.get("remaining"))
        data[ATTR_WALLET_SPEND] = to_float(wallet.get("spent"))
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
