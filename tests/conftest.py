"""Shared fixtures for Mozillion integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.config_entries import ConfigEntry

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
    CONF_REMAINING_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_SIM_NUMBER,
    CONF_SIM_PLAN_ID,
    CONF_TOTP_SECRET,
    CONF_USAGE_KEY,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
    DEFAULT_REMAINING_KEY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USAGE_KEY,
)


# ---------------------------------------------------------------------------
# Sample data returned by the Mozillion API
# ---------------------------------------------------------------------------
MOCK_API_RESPONSE: dict[str, Any] = {
    "usedData": 3.5,
    "totalData": 10.0,
    "isUnlimited": False,
    "planName": "Test Plan",
}

MOCK_API_RESPONSE_UNLIMITED: dict[str, Any] = {
    "usedData": 0.0,
    "totalData": 0.0,
    "isUnlimited": True,
    "planName": "Unlimited Plan",
}


# ---------------------------------------------------------------------------
# Config entry data that mirrors what the config flow produces
# ---------------------------------------------------------------------------
MOCK_ENTRY_DATA_LOGIN: dict[str, Any] = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret123",
    CONF_TOTP_SECRET: "",
    CONF_ORIGIN: DEFAULT_ORIGIN,
    CONF_ORDER_DETAIL_ID: "order-1",
    CONF_SIM_PLAN_ID: "sim-plan-1",
    CONF_SIM_NUMBER: "07700900000",
    CONF_SESSION_COOKIE: "",
    CONF_XSRF_TOKEN: "",
    CONF_USAGE_KEY: DEFAULT_USAGE_KEY,
    CONF_REMAINING_KEY: DEFAULT_REMAINING_KEY,
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
}

MOCK_ENTRY_DATA_COOKIE: dict[str, Any] = {
    CONF_EMAIL: "",
    CONF_PASSWORD: "",
    CONF_TOTP_SECRET: "",
    CONF_ORIGIN: DEFAULT_ORIGIN,
    CONF_ORDER_DETAIL_ID: "order-1",
    CONF_SIM_PLAN_ID: "sim-plan-1",
    CONF_SIM_NUMBER: "07700900000",
    CONF_SESSION_COOKIE: "mozillion_session=abc; XSRF-TOKEN=xyz",
    CONF_XSRF_TOKEN: "xyz",
    CONF_USAGE_KEY: DEFAULT_USAGE_KEY,
    CONF_REMAINING_KEY: DEFAULT_REMAINING_KEY,
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
}


# ---------------------------------------------------------------------------
# Coordinator data that the coordinator would produce from MOCK_API_RESPONSE
# ---------------------------------------------------------------------------
MOCK_COORDINATOR_DATA: dict[str, Any] = {
    ATTR_RAW: MOCK_API_RESPONSE,
    ATTR_USAGE: 3.5,
    ATTR_TOTAL: 10.0,
    ATTR_REMAINING: 6.5,
    ATTR_USAGE_PERCENTAGE: 35.0,
    ATTR_UNLIMITED: False,
    ATTR_SIM_NUMBER: "07700900000",
}

MOCK_COORDINATOR_DATA_UNLIMITED: dict[str, Any] = {
    ATTR_RAW: MOCK_API_RESPONSE_UNLIMITED,
    ATTR_USAGE: 0.0,
    ATTR_TOTAL: 0.0,
    ATTR_REMAINING: None,
    ATTR_USAGE_PERCENTAGE: None,
    ATTR_UNLIMITED: True,
    ATTR_SIM_NUMBER: "07700900000",
}


def _make_config_entry(
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    entry_id: str = "test_entry_id",
) -> ConfigEntry:
    """Create a mock ConfigEntry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.data = data or MOCK_ENTRY_DATA_COOKIE
    entry.options = options or {}
    entry.unique_id = entry.data.get(CONF_ORDER_DETAIL_ID, entry_id)
    entry.title = "Mozillion"
    return entry


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Return a mock config entry using cookie auth."""
    return _make_config_entry(data=MOCK_ENTRY_DATA_COOKIE)


@pytest.fixture
def mock_config_entry_login() -> ConfigEntry:
    """Return a mock config entry using login auth."""
    return _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)


@pytest.fixture
def mock_api_client() -> AsyncMock:
    """Return a mock MozillionClient."""
    client = AsyncMock()
    client.async_login.return_value = (
        "mozillion_session=abc; XSRF-TOKEN=xyz",
        "xyz",
    )
    client.async_get_usage.return_value = MOCK_API_RESPONSE
    client.async_fetch_dashboard_ids.return_value = [
        {
            "sim_plan_id": "sim-plan-1",
            "order_detail_id": "order-1",
            "name": "07700900000",
            "sim_number": "07700900000",
        }
    ]
    return client


@pytest.fixture
def mock_coordinator(mock_config_entry) -> MagicMock:
    """Return a mock MozillionCoordinator with data pre-loaded."""
    coordinator = MagicMock()
    coordinator.data = MOCK_COORDINATOR_DATA
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator
