"""Tests for the Mozillion coordinator."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError
from custom_components.mozillion import MozillionAuthError, MozillionCoordinator
from custom_components.mozillion.const import (
    ATTR_ICCID,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_SIM_NUMBER,
    ATTR_TOTAL,
    ATTR_TOTAL_GBR,
    ATTR_TOTAL_GLOBAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_GBR,
    ATTR_USAGE_GLOBAL,
    ATTR_USAGE_PERCENTAGE,
    AUTH_REFRESH_THRESHOLD,
    CONF_EMAIL,
    CONF_ORDER_DETAIL_ID,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_API_RESPONSE_UNLIMITED,
    MOCK_ENTRY_DATA_COOKIE,
    MOCK_ENTRY_DATA_LOGIN,
    _make_config_entry,
)


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

    coordinator = MozillionCoordinator.__new__(MozillionCoordinator)
    coordinator.client = client
    coordinator.config_entry = entry
    coordinator.cookie_header = cookie
    coordinator.xsrf_header = xsrf
    coordinator.email = entry.data.get(CONF_EMAIL)
    coordinator.password = entry.data.get(CONF_PASSWORD)
    coordinator.totp_secret = entry.data.get(CONF_TOTP_SECRET) or None
    coordinator.origin = entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN)
    coordinator._auth_time = None
    coordinator.hass = hass
    return coordinator


class TestCoordinatorUpdate:
    """Tests for the coordinator _async_update_data method."""

    @pytest.mark.asyncio
    async def test_successful_update(self) -> None:
        """Normal update returns processed data."""
        client = AsyncMock()
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
        client = AsyncMock()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()

        kwargs = client.async_get_usage.call_args.kwargs
        assert kwargs["order_detail_id"] == MOCK_ENTRY_DATA_COOKIE[CONF_ORDER_DETAIL_ID]
        assert kwargs["sim_meta_id"] == MOCK_ENTRY_DATA_COOKIE[CONF_SIM_META_ID]

    @pytest.mark.asyncio
    async def test_bucket_breakdown_is_exposed(self) -> None:
        """The GBR/global buckets are carried through as diagnostics."""
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()
        assert result[ATTR_SIM_NUMBER] == "07700900000"
        assert result[ATTR_ICCID] == "89440000000000000000"

    @pytest.mark.asyncio
    async def test_relogin_when_no_cookies(self) -> None:
        """Coordinator re-logs in when cookies are missing."""
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
        client.async_get_usage.side_effect = RuntimeError("API unreachable")
        coordinator = _make_coordinator(client)

        with pytest.raises(UpdateFailed, match="API unreachable"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_client_error_raises_update_failed(self) -> None:
        """ClientError is wrapped in UpdateFailed."""
        client = AsyncMock()
        client.async_get_usage.side_effect = ClientError("timeout")
        coordinator = _make_coordinator(client)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_proactive_refresh_when_session_stale(self) -> None:
        """Coordinator re-logs in before fetching when the session is stale."""
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
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
        client = AsyncMock()
        coordinator = _make_coordinator(client)
        coordinator.email = ""
        coordinator.password = ""

        assert coordinator._needs_auth() is False

    def test_known_fresh_session_is_reused(self) -> None:
        client = AsyncMock()
        coordinator = _make_coordinator(client, entry_data=MOCK_ENTRY_DATA_LOGIN)
        coordinator._auth_time = time.monotonic()

        assert coordinator._needs_auth() is False

    def test_unknown_auth_time_with_creds_forces_login(self) -> None:
        client = AsyncMock()
        coordinator = _make_coordinator(client, entry_data=MOCK_ENTRY_DATA_LOGIN)

        assert coordinator._needs_auth() is True
