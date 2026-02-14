"""Tests for the Mozillion coordinator (_async_update_data + setup/unload)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mozillion import _deep_get, MozillionCoordinator
from custom_components.mozillion.const import (
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_SIM_NUMBER,
    ATTR_TOTAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    CONF_EMAIL,
    CONF_ORDER_DETAIL_ID,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SIM_NUMBER,
    CONF_SIM_PLAN_ID,
    CONF_TOTP_SECRET,
    DEFAULT_ORIGIN,
    DOMAIN,
)

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
        """No cookies and no credentials raises UpdateFailed."""
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

        with pytest.raises(UpdateFailed):
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
