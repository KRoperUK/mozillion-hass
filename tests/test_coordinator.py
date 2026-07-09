"""Tests for the Mozillion coordinator (_async_update_data + setup/unload)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError
from custom_components.mozillion import (
    MozillionAuthError,
    MozillionCoordinator,
    _deep_get,
)
from custom_components.mozillion.const import (
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_SIM_NUMBER,
    ATTR_TOTAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    CONF_EMAIL,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
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

# ---------------------------------------------------------------------------
# _deep_get (comprehensive – extends existing tests)
# ---------------------------------------------------------------------------


class TestDeepGet:
    """Tests for the _deep_get helper."""

    def test_simple_key(self) -> None:
        assert _deep_get({"foo": 42}, "foo") == 42

    def test_dotted_key(self) -> None:
        assert _deep_get({"a": {"b": {"c": "deep"}}}, "a.b.c") == "deep"

    def test_missing_key(self) -> None:
        assert _deep_get({"a": {"b": 1}}, "a.x.y") is None

    def test_none_key(self) -> None:
        assert _deep_get({"a": 1}, None) is None

    def test_empty_key(self) -> None:
        assert _deep_get({"a": 1}, "") is None

    def test_non_dict_intermediate(self) -> None:
        assert _deep_get({"a": 123}, "a.b") is None

    def test_list_value(self) -> None:
        """Dotted key into a list returns None (not subscriptable by name)."""
        assert _deep_get({"a": [1, 2, 3]}, "a.0") is None

    def test_deeply_nested(self) -> None:
        data = {"l1": {"l2": {"l3": {"l4": "found"}}}}
        assert _deep_get(data, "l1.l2.l3.l4") == "found"

    def test_value_is_false(self) -> None:
        """False should be returned, not treated as missing."""
        assert _deep_get({"flag": False}, "flag") is False

    def test_value_is_zero(self) -> None:
        """Zero should be returned, not treated as missing."""
        assert _deep_get({"count": 0}, "count") == 0


# ---------------------------------------------------------------------------
# MozillionCoordinator._async_update_data
# ---------------------------------------------------------------------------


def _make_coordinator(
    client: AsyncMock,
    entry_data: dict[str, Any] | None = None,
    cookie: str | None = "cookie=abc",
    xsrf: str | None = "xsrf-tok",
    usage_key: str = "usedData",
    remaining_key: str = "totalData",
) -> MozillionCoordinator:
    """Create a coordinator with mocked dependencies."""
    hass = MagicMock()
    hass.loop = None  # Prevent real event loop usage
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = AsyncMock()
    entry = _make_config_entry(data=entry_data or MOCK_ENTRY_DATA_COOKIE)

    coordinator = MozillionCoordinator.__new__(MozillionCoordinator)
    coordinator.client = client
    coordinator.entry = entry
    coordinator.usage_key = usage_key
    coordinator.remaining_key = remaining_key
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
    async def test_unlimited_plan(self) -> None:
        """Unlimited plan data is processed correctly."""
        client = AsyncMock()
        client.async_get_usage.return_value = MOCK_API_RESPONSE_UNLIMITED
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result[ATTR_UNLIMITED] is True
        assert result[ATTR_USAGE] == 0.0
        assert result[ATTR_TOTAL] == 0.0
        # 0/0 → percentage should be 0 (guarded)
        assert result[ATTR_USAGE_PERCENTAGE] == 0

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
        # Cookie auth entry without cookies → error
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
    async def test_nested_usage_keys(self) -> None:
        """Dotted keys extract nested data."""
        client = AsyncMock()
        client.async_get_usage.return_value = {
            "data": {"used": 1.5, "total": 5.0},
            "isUnlimited": False,
        }
        coordinator = _make_coordinator(
            client, usage_key="data.used", remaining_key="data.total"
        )

        result = await coordinator._async_update_data()
        assert result[ATTR_USAGE] == 1.5
        assert result[ATTR_TOTAL] == 5.0
        assert result[ATTR_REMAINING] == 3.5

    @pytest.mark.asyncio
    async def test_missing_keys_give_none(self) -> None:
        """Missing usage keys result in None values."""
        client = AsyncMock()
        client.async_get_usage.return_value = {"otherField": 42}
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()
        assert result[ATTR_USAGE] is None
        assert result[ATTR_TOTAL] is None
        assert result[ATTR_REMAINING] is None
        assert result[ATTR_USAGE_PERCENTAGE] is None

    @pytest.mark.asyncio
    async def test_sim_number_in_output(self) -> None:
        """SIM number from entry data is included in output."""
        client = AsyncMock()
        client.async_get_usage.return_value = MOCK_API_RESPONSE
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()
        assert result[ATTR_SIM_NUMBER] == "07700900000"

    @pytest.mark.asyncio
    async def test_proactive_refresh_when_session_stale(self) -> None:
        """Coordinator re-logs in before fetching when the session is stale."""
        import time

        from custom_components.mozillion.const import AUTH_REFRESH_THRESHOLD

        client = AsyncMock()
        client.async_login.return_value = ("fresh-cookie", "fresh-xsrf")
        client.async_get_usage.return_value = MOCK_API_RESPONSE

        # Cookie present but credentials available and no known auth time → force
        # a proactive refresh.
        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="stale-cookie",
            xsrf="stale-xsrf",
        )
        # Far enough in the past (relative to monotonic) to exceed the threshold.
        coordinator._auth_time = time.monotonic() - (AUTH_REFRESH_THRESHOLD + 100)

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert coordinator.cookie_header == "fresh-cookie"
        assert coordinator.xsrf_header == "fresh-xsrf"
        # The fetch must use the freshly refreshed cookie, not the stale one.
        used_cookie = client.async_get_usage.call_args.kwargs["cookie_header"]
        assert used_cookie == "fresh-cookie"
        assert result[ATTR_USAGE] == 3.5

    @pytest.mark.asyncio
    async def test_relogin_on_auth_error_then_success(self) -> None:
        """Expired session triggers one re-login and a successful retry."""
        import time

        client = AsyncMock()
        client.async_login.return_value = ("new-cookie", "new-xsrf")

        # First usage attempt raises an auth error, retry succeeds.
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
        # Mark the session as freshly authenticated so the coordinator only
        # re-logs in because of the auth error, not proactively.
        coordinator._auth_time = time.monotonic()

        result = await coordinator._async_update_data()

        client.async_login.assert_called_once()
        assert coordinator.cookie_header == "new-cookie"
        assert result[ATTR_USAGE] == 3.5
        assert client.async_get_usage.call_count == 2

    @pytest.mark.asyncio
    async def test_relogin_on_auth_error_persists_session(self) -> None:
        """A successful re-login persists the refreshed session to the entry."""
        import time
        from unittest.mock import MagicMock

        client = AsyncMock()
        client.async_login.return_value = ("persisted-cookie", "persisted-xsrf")
        client.async_get_usage.side_effect = [
            MozillionAuthError("session expired"),
            MOCK_API_RESPONSE,
        ]

        # Coordinators in these tests use a MagicMock entry; give it the real
        # async_update_entry so we can assert the persisted data.
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)
        entry.async_update_entry = MagicMock()

        coordinator = MozillionCoordinator.__new__(MozillionCoordinator)
        coordinator.client = client
        coordinator.entry = entry
        coordinator.usage_key = "usedData"
        coordinator.remaining_key = "totalData"
        coordinator.cookie_header = "old-cookie"
        coordinator.xsrf_header = "old-xsrf"
        coordinator.email = entry.data.get(CONF_EMAIL)
        coordinator.password = entry.data.get(CONF_PASSWORD)
        coordinator.totp_secret = entry.data.get(CONF_TOTP_SECRET) or None
        coordinator.origin = entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN)
        # Fresh session → only the auth-error path triggers a re-login.
        coordinator._auth_time = time.monotonic()
        coordinator.hass = MagicMock()
        coordinator.hass.config_entries = MagicMock()
        coordinator.hass.config_entries.async_update_entry = entry.async_update_entry

        await coordinator._async_update_data()

        entry.async_update_entry.assert_called_once()
        persisted = entry.async_update_entry.call_args.kwargs["data"]
        assert persisted[CONF_SESSION_COOKIE] == "persisted-cookie"
        assert persisted[CONF_XSRF_TOKEN] == "persisted-xsrf"

    @pytest.mark.asyncio
    async def test_auth_error_without_creds_raises_update_failed(self) -> None:
        """Expired session with no credentials surfaces as ConfigEntryAuthFailed."""
        client = AsyncMock()
        client.async_get_usage.side_effect = MozillionAuthError("session expired")

        # Cookie-only entry (no email/password) cannot re-authenticate.
        coordinator = _make_coordinator(client, cookie="old-cookie", xsrf="old-xsrf")
        coordinator.email = ""
        coordinator.password = ""

        with pytest.raises(ConfigEntryAuthFailed, match="no credentials"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_auth_error_retry_still_fails_raises_update_failed(self) -> None:
        """A re-login that does not restore access fails cleanly."""
        client = AsyncMock()
        client.async_login.return_value = ("new-cookie", "new-xsrf")
        client.async_get_usage.side_effect = MozillionAuthError("still expired")

        coordinator = _make_coordinator(
            client,
            entry_data=MOCK_ENTRY_DATA_LOGIN,
            cookie="old-cookie",
            xsrf="old-xsrf",
        )

        with pytest.raises(UpdateFailed, match="did not restore access"):
            await coordinator._async_update_data()
