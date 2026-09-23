"""Tests for the Mozillion coordinator."""

from __future__ import annotations

import time
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError
from custom_components.mozillion import MozillionAuthError, MozillionCoordinator
from custom_components.mozillion.const import (
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
    CONF_EMAIL,
    CONF_ORDER_DETAIL_ID,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DASHBOARD_REFRESH_INTERVAL,
    DEFAULT_ORIGIN,
    REPAIR_FAILURE_THRESHOLD,
)
from custom_components.mozillion.coordinator import wallet_is_active
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_API_RESPONSE_UNLIMITED,
    MOCK_COORDINATOR_DATA_NO_WALLET,
    MOCK_ENTRY_DATA_COOKIE,
    MOCK_ENTRY_DATA_LOGIN,
    MOCK_SIM,
    MOCK_WALLET,
    _make_config_entry,
)


def _client() -> AsyncMock:
    """Build a client mock that answers every read the coordinator makes."""

    client = AsyncMock()
    client.async_get_usage.return_value = MOCK_API_RESPONSE
    client.async_fetch_sim.return_value = MOCK_SIM
    client.async_fetch_overspend.return_value = MOCK_WALLET
    return client


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

    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = AsyncMock()
    # A real dict, because the repair path reaches the issue registry via hass.data.
    hass.data = {}

    coordinator = MozillionCoordinator.__new__(MozillionCoordinator)
    coordinator.client = client
    coordinator.config_entry = entry
    coordinator.cookie_header = cookie
    coordinator.xsrf_header = xsrf
    coordinator.email = entry.data.get(CONF_EMAIL)
    coordinator.password = entry.data.get(CONF_PASSWORD)
    coordinator.totp_secret = entry.data.get(CONF_TOTP_SECRET) or None
    coordinator.origin = entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN)
    coordinator._reset_poll_state()
    coordinator.hass = hass
    return coordinator


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
        assert kwargs["order_detail_id"] == MOCK_ENTRY_DATA_COOKIE[CONF_ORDER_DETAIL_ID]
        assert kwargs["sim_meta_id"] == MOCK_ENTRY_DATA_COOKIE[CONF_SIM_META_ID]

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
    async def test_identity_fields_in_output(self) -> None:
        """SIM number and ICCID come from the entry, not the payload."""
        client = _client()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()
        assert result[ATTR_SIM_NUMBER] == "07700900000"
        assert result[ATTR_ICCID] == "89440000000000000000"

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
        assert coordinator.cookie_header == "new-cookie"
        assert coordinator.xsrf_header == "new-xsrf"
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
        coordinator.email = ""
        coordinator.password = ""

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
    async def test_client_error_raises_update_failed(self) -> None:
        """ClientError is wrapped in UpdateFailed."""
        client = _client()
        client.async_get_usage.side_effect = ClientError("timeout")
        coordinator = _make_coordinator(client)

        with pytest.raises(UpdateFailed):
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
        coordinator._auth_time = time.monotonic() - (AUTH_REFRESH_THRESHOLD + 100)

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert coordinator.cookie_header == "fresh-cookie"
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
        coordinator._auth_time = time.monotonic()

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert coordinator.cookie_header == "new-cookie"
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
        coordinator._auth_time = time.monotonic()
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
        coordinator.email = ""
        coordinator.password = ""

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
        """A failure on the retry that is not auth-related is just transient."""
        client = _client()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_get_usage.side_effect = [
            MozillionAuthError("expired"),
            ClientError("network blip"),
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


class TestNeedsAuth:
    """Tests for the session refresh heuristics."""

    def test_cookie_only_entry_never_relogins(self) -> None:
        client = _client()
        coordinator = _make_coordinator(client)
        coordinator.email = ""
        coordinator.password = ""

        assert coordinator._needs_auth() is False

    def test_known_fresh_session_is_reused(self) -> None:
        client = _client()
        coordinator = _make_coordinator(client, entry_data=MOCK_ENTRY_DATA_LOGIN)
        coordinator._auth_time = time.monotonic()

        assert coordinator._needs_auth() is False

    def test_unknown_auth_time_with_creds_forces_login(self) -> None:
        client = _client()
        coordinator = _make_coordinator(client, entry_data=MOCK_ENTRY_DATA_LOGIN)

        assert coordinator._needs_auth() is True


class TestAccountDetail:
    """The dashboard and wallet extras carried alongside usage."""

    @pytest.mark.asyncio
    async def test_dashboard_detail_is_exposed(self) -> None:
        coordinator = _make_coordinator(_client())

        result = await coordinator._async_update_data()

        assert result[ATTR_SIM_STATUS] == "ACTIVE"
        assert result[ATTR_RESET_LABEL] == "19 Oct"
        assert result[ATTR_RESET_DATE] == date(2026, 10, 19)
        assert result[ATTR_PLAN_TARIFF] == "10GB"
        assert result[ATTR_PLAN_DURATION] == "24-Months"
        assert result[ATTR_PLAN_ROAMING] == "EU roaming in 41 countries"
        assert result[ATTR_PLAN_TEXTS] == "Unlimited calls and texts"
        assert result[ATTR_PLAN_IS_DATA_ONLY] is False

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
        client.async_fetch_sim.side_effect = RuntimeError("markup changed")
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
        client.async_fetch_sim.side_effect = RuntimeError("no longer listed")

        coordinator = _make_coordinator(client)
        result = await coordinator._async_update_data()

        assert result[ATTR_USAGE] == 3.5
        assert result[ATTR_SIM_STATUS] == ""

    @pytest.mark.asyncio
    async def test_an_expired_session_still_propagates(self) -> None:
        """Extras are optional, but a dropped session needs the reauth flow."""
        client = _client()
        client.async_fetch_sim.side_effect = MozillionAuthError("login page")

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_COOKIE,
            cookie="old",
            xsrf="old",
        )
        coordinator.email = ""
        coordinator.password = ""

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_an_expired_session_on_an_extra_is_retried(self) -> None:
        """The retry must re-read all three, not just usage."""
        client = _client()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_fetch_sim.side_effect = [
            MozillionAuthError("expired"),
            MOCK_SIM,
        ]

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="old-cookie",
            xsrf="old-xsrf",
        )
        coordinator._auth_time = time.monotonic()

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
    """The 355 KB dashboard page is not re-read on every poll.

    Plan, service status and reset date change slowly, so the page has its own
    cadence while usage and the wallet keep the full poll interval.
    """

    async def test_second_poll_reuses_the_cached_detail(self) -> None:
        client = _client()
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()
        await coordinator._async_update_data()

        assert client.async_fetch_sim.await_count == 1
        # ...while the cheap JSON calls still happen every cycle.
        assert client.async_get_usage.await_count == 2
        assert client.async_fetch_overspend.await_count == 2

    async def test_detail_is_refreshed_once_the_interval_passes(self) -> None:
        client = _client()
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()
        coordinator._sim_detail_at = time.monotonic() - (DASHBOARD_REFRESH_INTERVAL + 1)
        await coordinator._async_update_data()

        assert client.async_fetch_sim.await_count == 2

    async def test_failed_refresh_keeps_the_last_reading(self) -> None:
        """One bad refresh must not blank the plan, status and reset entities."""
        client = _client()
        coordinator = _make_coordinator(client)
        await coordinator._async_update_data()

        client.async_fetch_sim.side_effect = RuntimeError("markup changed")
        coordinator._sim_detail_at = time.monotonic() - (DASHBOARD_REFRESH_INTERVAL + 1)
        result = await coordinator._async_update_data()

        assert result[ATTR_SIM_STATUS] == "ACTIVE"
        assert coordinator._dashboard_failures == 1

    async def test_cached_polls_do_not_inflate_the_failure_count(self) -> None:
        """Only refresh attempts count, or a broken page would trip the repair
        on the strength of polls that never tried to read it."""
        client = _client()
        coordinator = _make_coordinator(client)
        await coordinator._async_update_data()

        client.async_fetch_sim.side_effect = RuntimeError("markup changed")
        for _ in range(REPAIR_FAILURE_THRESHOLD + 1):
            await coordinator._async_update_data()

        assert coordinator._dashboard_failures == 0
