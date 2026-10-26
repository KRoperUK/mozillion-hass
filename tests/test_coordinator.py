"""Tests for the Mozillion coordinator."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError
from custom_components.mozillion import MozillionAuthError, MozillionCoordinator
from custom_components.mozillion import api as api_module
from custom_components.mozillion import coordinator as coordinator_module
from custom_components.mozillion.api import MozillionSim
from custom_components.mozillion.const import (
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
    AUTH_REFRESH_THRESHOLD,
    CONF_ORDER_DETAIL_ID,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_XSRF_TOKEN,
    DASHBOARD_REFRESH_INTERVAL,
    REPAIR_FAILURE_THRESHOLD,
    SUBENTRY_TYPE_SIM,
)
from custom_components.mozillion.coordinator import (
    MozillionDashboard,
    _coordinator_label,
    wallet_is_active,
)
from custom_components.mozillion.session import MozillionSession
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_API_RESPONSE_UNLIMITED,
    MOCK_COORDINATOR_DATA_NO_WALLET,
    MOCK_ENTRY_DATA_COOKIE,
    MOCK_ENTRY_DATA_LOGIN,
    MOCK_SIM,
    MOCK_SIM_SUBENTRY_DATA,
    MOCK_WALLET,
    _make_config_entry,
    _sim_subentry_data,
    sim_subentry,
)


def _client() -> AsyncMock:
    """Build a client mock that answers every read the coordinator makes."""

    client = AsyncMock()
    client.async_get_usage.return_value = MOCK_API_RESPONSE
    # The dashboard read returns every SIM on the account, as the real client does.
    client.async_fetch_sims.return_value = [MOCK_SIM]
    client.async_fetch_overspend.return_value = MOCK_WALLET
    return client


def _coordinator_for_subentry(
    client: AsyncMock,
    entry: Any,
    subentry: Any,
    session: MozillionSession,
    dashboard: MozillionDashboard,
) -> MozillionCoordinator:
    """Build a coordinator for one SIM over the account's shared objects."""

    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = AsyncMock()
    # A real dict, because the repair path reaches the issue registry via hass.data.
    hass.data = {}

    coordinator = MozillionCoordinator.__new__(MozillionCoordinator)
    coordinator.client = client
    coordinator.config_entry = entry
    coordinator.subentry = subentry
    coordinator.session = session
    coordinator.dashboard = dashboard
    coordinator._reset_poll_state()
    coordinator.hass = hass
    return coordinator


def _make_coordinator(
    client: AsyncMock,
    entry_data: dict[str, Any] | None = None,
    cookie: str | None = "cookie=abc",
    xsrf: str | None = "xsrf-tok",
) -> MozillionCoordinator:
    """Create a coordinator with mocked dependencies."""

    entry = _make_config_entry(data=entry_data or MOCK_ENTRY_DATA_COOKIE)
    return _make_coordinator_for_entry(client, entry, cookie=cookie, xsrf=xsrf)


def _make_coordinator_for_entry(
    client: AsyncMock,
    entry: Any,
    cookie: str | None = "cookie=abc",
    xsrf: str | None = "xsrf-tok",
) -> MozillionCoordinator:
    """Create a coordinator for a specific (mocked) config entry."""

    session = MozillionSession(cookie_header=cookie, xsrf_token=xsrf)
    return _coordinator_for_subentry(
        client,
        entry,
        sim_subentry(entry),
        session,
        MozillionDashboard(client=client, session=session),
    )


# A second SIM on the same account, told apart by the fields the tests read.
OTHER_SIM = replace(
    MOCK_SIM,
    sim_meta_id="7654322",
    order_detail_id="1234568",
    sim_number="07700900001",
    iccid="89440000000000000001",
    status="SUSPENDED",
    plan_data_tariff="20GB",
)


def _two_sim_coordinators(
    client: AsyncMock,
) -> tuple[MozillionCoordinator, MozillionCoordinator]:
    """Two SIMs on one account, sharing one session and one dashboard read."""

    entry = _make_config_entry(
        data=MOCK_ENTRY_DATA_COOKIE,
        subentries=[
            _sim_subentry_data(),
            _sim_subentry_data(
                order_detail_id=OTHER_SIM.order_detail_id,
                sim_meta_id=OTHER_SIM.sim_meta_id,
                sim_number=OTHER_SIM.sim_number,
                iccid=OTHER_SIM.iccid,
            ),
        ],
    )
    subentries = {
        str(subentry.data[CONF_SIM_META_ID]): subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_SIM
    }
    session = MozillionSession(cookie_header="cookie=abc", xsrf_token="xsrf-tok")
    # One holder for the account, built the way async_setup_entry builds it.
    dashboard = MozillionDashboard(client=client, session=session)
    return (
        _coordinator_for_subentry(
            client, entry, subentries[MOCK_SIM.sim_meta_id], session, dashboard
        ),
        _coordinator_for_subentry(
            client, entry, subentries[OTHER_SIM.sim_meta_id], session, dashboard
        ),
    )


class TestCoordinatorUpdate:
    """Tests for the coordinator _async_update_data method."""

    @pytest.mark.asyncio
    async def test_successful_update(self) -> None:
        """Normal update returns processed data."""
        client = _client()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result[ATTR_USAGE] == 3.5
        assert result[ATTR_TOTAL] == 10.0
        assert result[ATTR_REMAINING] == 6.5
        assert result[ATTR_USAGE_PERCENTAGE] == 35.0
        assert result[ATTR_UNLIMITED] is False
        assert result[ATTR_RAW] == MOCK_API_RESPONSE
        client.async_get_usage.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_the_new_id_pair_to_the_client(self) -> None:
        """The trigger endpoint needs both the order and meta ids."""
        client = _client()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()

        kwargs = client.async_get_usage.call_args.kwargs
        assert kwargs["order_detail_id"] == MOCK_SIM_SUBENTRY_DATA[CONF_ORDER_DETAIL_ID]
        assert kwargs["sim_meta_id"] == MOCK_SIM_SUBENTRY_DATA[CONF_SIM_META_ID]

    @pytest.mark.asyncio
    async def test_bucket_breakdown_is_exposed(self) -> None:
        """The GBR/global buckets are carried through as diagnostics."""
        client = _client()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result[ATTR_USAGE_GBR] == 3.5
        assert result[ATTR_TOTAL_GBR] == 10.0
        assert result[ATTR_USAGE_GLOBAL] == 0.0
        assert result[ATTR_TOTAL_GLOBAL] == 0.0

    @pytest.mark.asyncio
    async def test_unlimited_plan(self) -> None:
        """A zero allowance is unknown rather than a divide-by-zero."""
        client = _client()
        client.async_get_usage.return_value = MOCK_API_RESPONSE_UNLIMITED
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result[ATTR_UNLIMITED] is True
        assert result[ATTR_USAGE] == 5.0
        assert result[ATTR_REMAINING] is None
        assert result[ATTR_USAGE_PERCENTAGE] is None

    @pytest.mark.asyncio
    async def test_over_allowance_is_clamped(self) -> None:
        """Mirror the dashboard: no negative balance and never above 100%."""
        client = _client()
        client.async_get_usage.return_value = {
            "status": "success",
            "usedData": 12.0,
            "totalData": 10.0,
            "isUnlimited": False,
        }
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result[ATTR_REMAINING] == 0.0
        assert result[ATTR_USAGE_PERCENTAGE] == 100.0

    @pytest.mark.asyncio
    async def test_missing_keys_give_none(self) -> None:
        """Missing usage keys result in None values."""
        client = _client()
        client.async_get_usage.return_value = {"status": "success"}
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()
        assert result[ATTR_USAGE] is None
        assert result[ATTR_TOTAL] is None
        assert result[ATTR_REMAINING] is None
        assert result[ATTR_USAGE_PERCENTAGE] is None
        assert result[ATTR_UNLIMITED] is False

    @pytest.mark.asyncio
    async def test_identity_fields_not_in_output(self) -> None:
        """The phone number and ICCID stay out of the polled data.

        Nothing reads them any more, and entity attributes are readable by every
        user on the instance, recorded, and pasted into bug reports.
        """
        client = _client()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()
        assert ATTR_SIM_NUMBER not in result
        assert ATTR_ICCID not in result

    @pytest.mark.asyncio
    async def test_relogin_when_no_cookies(self) -> None:
        """Coordinator re-logs in when cookies are missing."""
        client = _client()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_get_usage.return_value = MOCK_API_RESPONSE

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie=None,
            xsrf=None,
        )

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert coordinator.session.cookie_header == "new-cookie"
        assert coordinator.session.xsrf_token == "new-xsrf"
        assert result[ATTR_USAGE] == 3.5

    @pytest.mark.asyncio
    async def test_no_cookies_no_creds_raises(self) -> None:
        """No cookies and no credentials raises ConfigEntryAuthFailed."""
        client = _client()
        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_COOKIE,
            cookie=None,
            xsrf=None,
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_api_error_raises_update_failed(self) -> None:
        """API error is wrapped in UpdateFailed."""
        client = _client()
        client.async_get_usage.side_effect = RuntimeError("API unreachable")
        coordinator = _make_coordinator(client)

        with pytest.raises(UpdateFailed, match="API unreachable"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_a_leaked_client_error_is_not_swallowed(self) -> None:
        """MozillionClient converts ClientError into RuntimeError at its boundary.

        The coordinator deliberately does not catch aiohttp's exception: if one
        ever gets past the client that is a boundary bug, so it must surface
        rather than be quietly turned into an ordinary UpdateFailed.
        """
        client = _client()
        client.async_get_usage.side_effect = ClientError("timeout")
        coordinator = _make_coordinator(client)

        with pytest.raises(ClientError):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_proactive_refresh_when_session_stale(self) -> None:
        """Coordinator re-logs in before fetching when the session is stale."""
        client = _client()
        client.async_login.return_value = ("fresh-cookie", "fresh-xsrf")
        client.async_get_usage.return_value = MOCK_API_RESPONSE

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="stale-cookie",
            xsrf="stale-xsrf",
        )
        coordinator.session.authenticated_at = time.monotonic() - (
            AUTH_REFRESH_THRESHOLD + 100
        )

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert coordinator.session.cookie_header == "fresh-cookie"
        # The fetch must use the freshly refreshed cookie, not the stale one.
        assert client.async_get_usage.call_args.kwargs["cookie_header"] == (
            "fresh-cookie"
        )
        assert result[ATTR_USAGE] == 3.5

    @pytest.mark.asyncio
    async def test_relogin_on_auth_error_then_success(self) -> None:
        """Expired session triggers one re-login and a successful retry."""
        client = _client()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_get_usage.side_effect = [
            MozillionAuthError("session expired"),
            MOCK_API_RESPONSE,
        ]

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="old-cookie",
            xsrf="old-xsrf",
        )
        # Fresh session → only the auth-error path triggers a re-login.
        coordinator.session.authenticated_at = time.monotonic()

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert coordinator.session.cookie_header == "new-cookie"
        assert result[ATTR_USAGE] == 3.5
        assert client.async_get_usage.call_count == 2

    @pytest.mark.asyncio
    async def test_relogin_on_auth_error_persists_session(self) -> None:
        """A successful re-login persists the refreshed session to the entry."""
        client = _client()
        client.async_login.return_value = ("persisted-cookie", "persisted-xsrf")
        client.async_get_usage.side_effect = [
            MozillionAuthError("session expired"),
            MOCK_API_RESPONSE,
        ]

        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)
        entry.async_update_entry = MagicMock()
        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_update_entry = entry.async_update_entry

        coordinator = _make_coordinator_for_entry(client, entry)
        coordinator.session.authenticated_at = time.monotonic()
        coordinator.hass = hass

        await coordinator._async_update_data()

        entry.async_update_entry.assert_called_once()
        persisted = entry.async_update_entry.call_args.kwargs["data"]
        assert persisted[CONF_SESSION_COOKIE] == "persisted-cookie"
        assert persisted[CONF_XSRF_TOKEN] == "persisted-xsrf"

    @pytest.mark.asyncio
    async def test_auth_error_without_creds_raises_auth_failed(self) -> None:
        """Expired session with no credentials asks the user to re-auth."""
        client = _client()
        client.async_get_usage.side_effect = MozillionAuthError("session expired")

        coordinator = _make_coordinator(client, cookie="old-cookie", xsrf="old-xsrf")

        with pytest.raises(ConfigEntryAuthFailed, match="no credentials"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_auth_error_after_relogin_asks_the_user(self) -> None:
        """A re-login that is still rejected means the credentials are wrong.

        Retrying cannot fix that, so it must surface as a reauth request rather
        than a transient UpdateFailed.
        """
        client = _client()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_get_usage.side_effect = MozillionAuthError("still expired")

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="old-cookie",
            xsrf="old-xsrf",
        )

        with pytest.raises(ConfigEntryAuthFailed, match="rejected the configured"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_auth_error_then_network_error_is_transient(self) -> None:
        """A failure on the retry that is not auth-related is just transient.

        MozillionClient turns aiohttp's ClientError into a RuntimeError at the
        API boundary, so that is the exception a network blip arrives as.
        """
        client = _client()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_get_usage.side_effect = [
            MozillionAuthError("expired"),
            RuntimeError("Error communicating with Mozillion: network blip"),
        ]

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="old-cookie",
            xsrf="old-xsrf",
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_auth_error_then_login_failure_is_transient(self) -> None:
        """A re-login that never completes is transient, not a credential error."""
        client = _client()
        client.async_login.side_effect = RuntimeError("site unreachable")
        client.async_get_usage.side_effect = MozillionAuthError("expired")

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="old-cookie",
            xsrf="old-xsrf",
        )

        with pytest.raises(UpdateFailed, match="site unreachable"):
            await coordinator._async_update_data()


class TestSharedParsing:
    """Number parsing lives in exactly one place (#71).

    The coordinator used to carry its own copy, which had already drifted from
    the API's: it accepted ``Any`` and swallowed ``TypeError``.
    """

    def test_the_coordinator_uses_the_api_parser(self) -> None:
        assert coordinator_module.to_float is api_module.to_float

    def test_the_local_copy_is_gone(self) -> None:
        assert not hasattr(coordinator_module, "_to_float")


class TestNeedsAuth:
    """Tests for the shared session's refresh heuristics.

    One session covers every SIM on the account, so these decide for the account
    rather than for a single coordinator.
    """

    def test_cookie_only_entry_never_relogins(self) -> None:
        coordinator = _make_coordinator(_client(), entry_data=MOCK_ENTRY_DATA_COOKIE)

        assert (
            coordinator.session.needs_authentication(coordinator.config_entry) is False
        )

    def test_known_fresh_session_is_reused(self) -> None:
        coordinator = _make_coordinator(_client(), entry_data=MOCK_ENTRY_DATA_LOGIN)
        coordinator.session.authenticated_at = time.monotonic()

        assert (
            coordinator.session.needs_authentication(coordinator.config_entry) is False
        )

    def test_unknown_auth_time_with_creds_forces_login(self) -> None:
        coordinator = _make_coordinator(_client(), entry_data=MOCK_ENTRY_DATA_LOGIN)

        assert (
            coordinator.session.needs_authentication(coordinator.config_entry) is True
        )


class TestAccountDetail:
    """The dashboard and wallet extras carried alongside usage."""

    @pytest.mark.asyncio
    async def test_dashboard_detail_is_exposed(self, frozen_today: date) -> None:
        coordinator = _make_coordinator(_client())

        result = await coordinator._async_update_data()

        assert result[ATTR_SIM_STATUS] == "ACTIVE"
        assert result[ATTR_RESET_LABEL] == "19 Oct"
        # "19 Oct" rolls forward from the pinned 2026-09-21, never from the wall
        # clock: the next 19th is 2026-10-19.
        assert result[ATTR_RESET_DATE] == date(2026, 10, 19)
        assert result[ATTR_PLAN_TARIFF] == "10GB"
        assert result[ATTR_PLAN_DURATION] == "24-Months"
        assert result[ATTR_PLAN_ROAMING] == "EU roaming in 41 countries"
        assert result[ATTR_PLAN_TEXTS] == "Unlimited calls and texts"
        assert result[ATTR_PLAN_IS_DATA_ONLY] is False

    @pytest.mark.asyncio
    async def test_reset_date_uses_home_assistants_date_not_the_process_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reset date follows Home Assistant's time zone, not the container's.

        In Docker the process is usually UTC while Home Assistant is configured
        for a non-UTC zone, so for the first hours of a local day ``date.today()``
        is still yesterday. Both clocks are pinned a day apart and the answer
        must follow Home Assistant's, which is the one ahead.
        """

        process_today = date(2026, 9, 21)

        class _ProcessDate(date):
            @classmethod
            def today(cls) -> date:
                return process_today

        # The fallback ``parse_reset_date`` would use on its own.
        monkeypatch.setattr("custom_components.mozillion.api.date", _ProcessDate)
        # Home Assistant's clock: already in the configured zone, so its date is
        # the one the user is living in -- here 00:30 on the 22nd while the
        # process is still on the 21st.
        monkeypatch.setattr(
            "custom_components.mozillion.coordinator.dt_util",
            SimpleNamespace(now=lambda: datetime(2026, 9, 22, 0, 30, tzinfo=UTC)),
            raising=False,
        )

        client = _client()
        client.async_fetch_sims.return_value = [replace(MOCK_SIM, days_left="19")]

        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result[ATTR_DAYS_LEFT] == "19"
        # 2026-09-22 + 19 days. The process clock would have said 2026-10-10.
        assert result[ATTR_RESET_DATE] == date(2026, 10, 11)

    @pytest.mark.asyncio
    async def test_wallet_detail_is_exposed(self) -> None:
        coordinator = _make_coordinator(_client())

        result = await coordinator._async_update_data()

        assert result[ATTR_WALLET_BALANCE] == 9.5
        assert result[ATTR_WALLET_SPEND] == 2.5
        assert result[ATTR_OVERSPEND_LIMIT_REACHED] is False
        assert result[ATTR_WALLET] == MOCK_WALLET

    @pytest.mark.asyncio
    async def test_missing_extras_leave_usage_intact(self) -> None:
        """The extras come from fragile HTML; usage must survive without them."""
        client = _client()
        client.async_fetch_sims.side_effect = RuntimeError("markup changed")
        client.async_fetch_overspend.side_effect = RuntimeError("site down")

        coordinator = _make_coordinator(client)
        result = await coordinator._async_update_data()

        assert result[ATTR_USAGE] == 3.5
        assert result[ATTR_SIM_STATUS] == ""
        assert result[ATTR_RESET_DATE] is None
        assert result[ATTR_WALLET] is None
        assert result[ATTR_WALLET_BALANCE] is None

    @pytest.mark.asyncio
    async def test_a_removed_sim_does_not_break_usage(self) -> None:
        client = _client()
        client.async_fetch_sims.side_effect = RuntimeError("no longer listed")

        coordinator = _make_coordinator(client)
        result = await coordinator._async_update_data()

        assert result[ATTR_USAGE] == 3.5
        assert result[ATTR_SIM_STATUS] == ""

    @pytest.mark.asyncio
    async def test_an_expired_session_still_propagates(self) -> None:
        """Extras are optional, but a dropped session needs the reauth flow."""
        client = _client()
        client.async_fetch_sims.side_effect = MozillionAuthError("login page")

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_COOKIE,
            cookie="old",
            xsrf="old",
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_an_expired_session_on_an_extra_is_retried(self) -> None:
        """The retry must re-read all three, not just usage."""
        client = _client()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_fetch_sims.side_effect = [
            MozillionAuthError("expired"),
            [MOCK_SIM],
        ]

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="old-cookie",
            xsrf="old-xsrf",
        )
        coordinator.session.authenticated_at = time.monotonic()

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert result[ATTR_SIM_STATUS] == "ACTIVE"
        assert result[ATTR_USAGE] == 3.5


class TestWalletIsActive:
    """Mozillion hides the wallet until it has been topped up."""

    def test_positive_balance_is_active(self) -> None:
        assert (
            wallet_is_active(
                {
                    ATTR_WALLET: MOCK_WALLET,
                    ATTR_WALLET_BALANCE: 9.5,
                    ATTR_WALLET_SPEND: 0.0,
                }
            )
            is True
        )

    def test_spend_with_no_balance_is_still_active(self) -> None:
        """A fully spent wallet is exactly when the figure matters."""
        assert (
            wallet_is_active(
                {
                    ATTR_WALLET: MOCK_WALLET,
                    ATTR_WALLET_BALANCE: 0.0,
                    ATTR_WALLET_SPEND: 3.0,
                }
            )
            is True
        )

    def test_never_topped_up_is_inactive(self) -> None:
        assert wallet_is_active(MOCK_COORDINATOR_DATA_NO_WALLET) is False

    def test_unread_wallet_is_inactive(self) -> None:
        assert wallet_is_active({ATTR_WALLET: None}) is False


class TestDashboardCache:
    """The 355 KB dashboard page is not re-read on every poll, or for every SIM.

    Plan, service status and reset date change slowly, so the page has its own
    cadence while usage and the wallet keep the full poll interval. The page also
    carries every SIM on the account, so the cache lives on the account's
    ``MozillionDashboard`` (issue #73): one read serves all of them.
    """

    @staticmethod
    def _expire(dashboard: MozillionDashboard) -> None:
        """Age the account's last dashboard read past DASHBOARD_REFRESH_INTERVAL."""

        dashboard._fetched_at = time.monotonic() - (DASHBOARD_REFRESH_INTERVAL + 1)

    async def test_second_poll_reuses_the_cached_detail(self) -> None:
        client = _client()
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()
        await coordinator._async_update_data()

        assert client.async_fetch_sims.await_count == 1
        # ...while the cheap JSON calls still happen every cycle.
        assert client.async_get_usage.await_count == 2
        assert client.async_fetch_overspend.await_count == 2

    async def test_detail_is_refreshed_once_the_interval_passes(self) -> None:
        client = _client()
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()
        self._expire(coordinator.dashboard)
        await coordinator._async_update_data()

        assert client.async_fetch_sims.await_count == 2

    async def test_failed_refresh_keeps_the_last_reading(self) -> None:
        """One bad refresh must not blank the plan, status and reset entities."""
        client = _client()
        coordinator = _make_coordinator(client)
        await coordinator._async_update_data()

        client.async_fetch_sims.side_effect = RuntimeError("markup changed")
        self._expire(coordinator.dashboard)
        result = await coordinator._async_update_data()

        assert result[ATTR_SIM_STATUS] == "ACTIVE"
        assert coordinator._dashboard_failures == 1

    async def test_cached_polls_do_not_inflate_the_failure_count(self) -> None:
        """Only refresh attempts count, or a broken page would trip the repair
        on the strength of polls that never tried to read it."""
        client = _client()
        coordinator = _make_coordinator(client)
        await coordinator._async_update_data()

        client.async_fetch_sims.side_effect = RuntimeError("markup changed")
        for _ in range(REPAIR_FAILURE_THRESHOLD + 1):
            await coordinator._async_update_data()

        assert coordinator._dashboard_failures == 0

    async def test_one_read_serves_two_sims_on_the_account(self) -> None:
        """Issue #73: the page lists every SIM, so N SIMs read it once, not N times."""
        client = _client()
        client.async_fetch_sims.return_value = [MOCK_SIM, OTHER_SIM]
        first, second = _two_sim_coordinators(client)

        first_result = await first._async_update_data()
        second_result = await second._async_update_data()

        assert client.async_fetch_sims.await_count == 1
        # Each SIM still gets its own detail out of the one page.
        assert first_result[ATTR_SIM_STATUS] == "ACTIVE"
        assert first_result[ATTR_PLAN_TARIFF] == "10GB"
        assert second_result[ATTR_SIM_STATUS] == "SUSPENDED"
        assert second_result[ATTR_PLAN_TARIFF] == "20GB"

    async def test_two_sims_polling_together_make_one_read(self) -> None:
        """A refresh cycle polls both coordinators; the account reads once."""
        client = _client()
        client.async_fetch_sims.return_value = [MOCK_SIM, OTHER_SIM]
        first, second = _two_sim_coordinators(client)

        await asyncio.gather(first._async_update_data(), second._async_update_data())
        assert client.async_fetch_sims.await_count == 1

        # Both poll again after the window: one read serves the pair once more,
        # rather than two per cycle.
        self._expire(first.dashboard)
        await asyncio.gather(first._async_update_data(), second._async_update_data())

        assert client.async_fetch_sims.await_count == 2

    async def test_a_failed_read_is_not_cached(self) -> None:
        """The next SIM retries instead of being served the cached error."""
        client = _client()
        client.async_fetch_sims.side_effect = RuntimeError("markup changed")
        first, second = _two_sim_coordinators(client)

        first_result = await first._async_update_data()

        assert first_result[ATTR_SIM_STATUS] == ""
        assert first.dashboard.cached(MOCK_SIM.sim_meta_id) is None
        assert first._dashboard_failures == 1

        client.async_fetch_sims.side_effect = None
        client.async_fetch_sims.return_value = [MOCK_SIM, OTHER_SIM]
        second_result = await second._async_update_data()

        # Two attempts: the failure left nothing behind for the second SIM.
        assert client.async_fetch_sims.await_count == 2
        assert second_result[ATTR_SIM_STATUS] == "SUSPENDED"
        # ...and the second SIM's success does not clear the first SIM's count,
        # because that SIM's own entities are the ones affected.
        assert first._dashboard_failures == 1


class TestMozillionDashboard:
    """The shared holder on its own, where the fetch count is unambiguous."""

    @staticmethod
    def _dashboard(client: AsyncMock) -> MozillionDashboard:
        return MozillionDashboard(
            client=client,
            session=MozillionSession(cookie_header="cookie=abc", xsrf_token="xsrf-tok"),
        )

    async def test_reads_are_passed_the_shared_session(self) -> None:
        client = _client()
        dashboard = self._dashboard(client)

        await dashboard.async_get(MOCK_SIM.sim_meta_id)

        kwargs = client.async_fetch_sims.call_args.kwargs
        assert kwargs["cookie_header"] == "cookie=abc"
        assert kwargs["xsrf_token"] == "xsrf-tok"

    async def test_the_lock_makes_two_simultaneous_reads_fetch_once(self) -> None:
        """Without the lock the second caller would download the page too."""
        client = _client()
        dashboard = self._dashboard(client)

        in_flight = asyncio.Event()
        release = asyncio.Event()

        async def slow_fetch(**_kwargs: Any) -> list[MozillionSim]:
            in_flight.set()
            await release.wait()
            return [MOCK_SIM, OTHER_SIM]

        client.async_fetch_sims.side_effect = slow_fetch

        first = asyncio.create_task(dashboard.async_get(MOCK_SIM.sim_meta_id))
        await in_flight.wait()
        second = asyncio.create_task(dashboard.async_get(OTHER_SIM.sim_meta_id))
        # Give the second caller every chance to run: it reaches the lock while the
        # first is still fetching, and parks there.
        for _ in range(10):
            await asyncio.sleep(0)
        assert not second.done()

        release.set()
        sims = await asyncio.gather(first, second)

        # One fetch, not two -- and both callers got their own SIM out of it.
        assert client.async_fetch_sims.await_count == 1
        assert [sim.sim_meta_id for sim in sims] == [
            MOCK_SIM.sim_meta_id,
            OTHER_SIM.sim_meta_id,
        ]

    async def test_a_stale_reading_is_not_served_as_fresh(self) -> None:
        client = _client()
        dashboard = self._dashboard(client)
        await dashboard.async_get(MOCK_SIM.sim_meta_id)

        TestDashboardCache._expire(dashboard)

        assert dashboard.cached(MOCK_SIM.sim_meta_id) is None
        # ...but it is still the fallback for a failed refresh.
        assert dashboard.last_known(MOCK_SIM.sim_meta_id) == MOCK_SIM

    async def test_a_sim_missing_from_the_page_is_reported(self) -> None:
        client = _client()
        dashboard = self._dashboard(client)

        with pytest.raises(RuntimeError, match="no longer listed"):
            await dashboard.async_get(OTHER_SIM.sim_meta_id)

        # A page that parsed fine is still a success, so it is cached.
        assert dashboard.cached(MOCK_SIM.sim_meta_id) == MOCK_SIM


class TestCoordinatorLabel:
    """The coordinator's name reaches Home Assistant's own log lines."""

    @staticmethod
    def _subentry(**data: str) -> Any:
        from homeassistant.config_entries import ConfigSubentry

        return ConfigSubentry(
            data=MappingProxyType(data),
            subentry_type=SUBENTRY_TYPE_SIM,
            title=data.get(CONF_SIM_NUMBER, "SIM"),
            unique_id="7654321",
        )

    def test_the_phone_number_is_masked(self) -> None:
        label = _coordinator_label(self._subentry(**{CONF_SIM_NUMBER: "07700900000"}))

        assert "07700900000" not in label
        assert label.startswith("077")
        assert label.endswith("000")

    def test_a_sim_with_no_number_still_gets_a_label(self) -> None:
        assert _coordinator_label(self._subentry()) == SUBENTRY_TYPE_SIM
