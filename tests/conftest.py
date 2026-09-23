"""Shared fixtures for Mozillion integration tests.

The config entry is the *account* (credentials and session); each SIM is a subentry of
type ``sim``, mirroring how the integration is actually structured.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.mozillion.api import MozillionSim
from custom_components.mozillion.const import (
    ATTR_BILLING_AMOUNT,
    ATTR_BILLING_DAYS,
    ATTR_DAYS_LEFT,
    ATTR_HAS_BILL,
    ATTR_ICCID,
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
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

# ---------------------------------------------------------------------------
# Sample data returned by the Mozillion usage endpoints
# ---------------------------------------------------------------------------
MOCK_API_RESPONSE: dict[str, Any] = {
    "status": "success",
    "usedData": 3.5,
    "totalData": 10.0,
    "usedDataGbr": 3.5,
    "totalDataGbr": 10.0,
    "usedDataGlobal": 0.0,
    "totalDataGlobal": 0.0,
    "isUnlimited": False,
}

MOCK_API_RESPONSE_UNLIMITED: dict[str, Any] = {
    "status": "success",
    "usedData": 5.0,
    "totalData": 0,
    "usedDataGbr": 5.0,
    "totalDataGbr": 0,
    "usedDataGlobal": 0.0,
    "totalDataGlobal": 0.0,
    "isUnlimited": True,
}

# The trigger endpoint answers this while the figures are being regenerated.
MOCK_API_RESPONSE_PENDING: dict[str, Any] = {
    "status": "pending",
    "message": "Usage update is being processed.",
}


# ---------------------------------------------------------------------------
# Sample dashboard SIM
# ---------------------------------------------------------------------------
MOCK_SIM = MozillionSim(
    sim_meta_id="7654321",
    order_detail_id="1234567",
    sim_number="07700900000",
    iccid="89440000000000000000",
    label="Your SIM",
    status="ACTIVE",
    reset_label="19 Oct",
    days_left="",
    used_data=3.5,
    total_data=10.0,
    used_data_gbr=3.5,
    total_data_gbr=10.0,
    used_data_global=0.0,
    total_data_global=0.0,
    is_unlimited=False,
    plan_duration="24-Months",
    plan_data_tariff="10GB",
    plan_roaming="EU roaming in 41 countries",
    plan_texts_minutes="Unlimited calls and texts",
    plan_voicemail=True,
    plan_parental_control=False,
    port_status="DONE",
    port_status_label="Done",
    port_status_description="Your number has been successfully transferred.",
    port_date="29-12-2025",
    billing_amount=999.0,
    has_bill=False,
    billing_days="",
    plan_is_data_only=False,
)

# The overspend endpoint, as the dashboard's own wallet button reads it.
MOCK_WALLET: dict[str, Any] = {
    "success": True,
    "balance": 12.0,
    "spent": 2.5,
    "remaining": 9.5,
    "reached": False,
}

# A SIM that has never had a wallet top-up: all zeros with the limit "reached".
MOCK_WALLET_INACTIVE: dict[str, Any] = {
    "success": True,
    "balance": 0,
    "spent": 0,
    "remaining": 0,
    "reached": True,
}


# ---------------------------------------------------------------------------
# Config entry data: the account, plus one SIM subentry
# ---------------------------------------------------------------------------
MOCK_ENTRY_DATA_LOGIN: dict[str, Any] = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret123",
    CONF_TOTP_SECRET: "",
    CONF_ORIGIN: DEFAULT_ORIGIN,
    CONF_SESSION_COOKIE: "",
    CONF_XSRF_TOKEN: "",
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
}

MOCK_ENTRY_DATA_COOKIE: dict[str, Any] = {
    **MOCK_ENTRY_DATA_LOGIN,
    CONF_EMAIL: "",
    CONF_PASSWORD: "",
    CONF_SESSION_COOKIE: "mozillion_session=abc; XSRF-TOKEN=xyz",
    CONF_XSRF_TOKEN: "xyz",
}

MOCK_SIM_SUBENTRY_DATA: dict[str, Any] = {
    CONF_ORDER_DETAIL_ID: "1234567",
    CONF_SIM_META_ID: "7654321",
    CONF_SIM_NUMBER: "07700900000",
    CONF_ICCID: "89440000000000000000",
}


def _sim_subentry_data(**overrides: Any) -> dict[str, Any]:
    """Build a flow-style SIM subentry payload for MockConfigEntry."""

    data = {**MOCK_SIM_SUBENTRY_DATA, **overrides}
    return {
        "subentry_type": SUBENTRY_TYPE_SIM,
        "data": data,
        "title": data.get(CONF_SIM_NUMBER) or "SIM",
        "unique_id": data[CONF_SIM_META_ID],
    }


# ---------------------------------------------------------------------------
# Coordinator data the coordinator produces from MOCK_API_RESPONSE
# ---------------------------------------------------------------------------
MOCK_COORDINATOR_DATA: dict[str, Any] = {
    ATTR_RAW: MOCK_API_RESPONSE,
    ATTR_USAGE: 3.5,
    ATTR_TOTAL: 10.0,
    ATTR_REMAINING: 6.5,
    ATTR_USAGE_PERCENTAGE: 35.0,
    ATTR_UNLIMITED: False,
    ATTR_USAGE_GBR: 3.5,
    ATTR_TOTAL_GBR: 10.0,
    ATTR_USAGE_GLOBAL: 0.0,
    ATTR_TOTAL_GLOBAL: 0.0,
    ATTR_SIM_NUMBER: "07700900000",
    ATTR_ICCID: "89440000000000000000",
    ATTR_SIM_STATUS: "ACTIVE",
    ATTR_RESET_DATE: date(2026, 10, 19),
    ATTR_RESET_LABEL: "19 Oct",
    ATTR_DAYS_LEFT: "",
    ATTR_PLAN_TARIFF: "10GB",
    ATTR_PLAN_DURATION: "24-Months",
    ATTR_PLAN_ROAMING: "EU roaming in 41 countries",
    ATTR_PLAN_TEXTS: "Unlimited calls and texts",
    ATTR_PLAN_IS_DATA_ONLY: False,
    ATTR_PLAN_VOICEMAIL: True,
    ATTR_PLAN_PARENTAL_CONTROL: False,
    ATTR_PORT_STATUS: "DONE",
    ATTR_PORT_STATUS_LABEL: "Done",
    ATTR_PORT_STATUS_DESCRIPTION: "Your number has been successfully transferred.",
    ATTR_PORT_DATE: "29-12-2025",
    ATTR_BILLING_AMOUNT: 999.0,
    ATTR_HAS_BILL: False,
    ATTR_BILLING_DAYS: "",
    ATTR_WALLET: MOCK_WALLET,
    ATTR_WALLET_BALANCE: 9.5,
    ATTR_WALLET_SPEND: 2.5,
    ATTR_OVERSPEND_LIMIT_REACHED: False,
}

MOCK_COORDINATOR_DATA_UNLIMITED: dict[str, Any] = {
    **MOCK_COORDINATOR_DATA,
    ATTR_RAW: MOCK_API_RESPONSE_UNLIMITED,
    ATTR_USAGE: 5.0,
    ATTR_TOTAL: 0.0,
    ATTR_REMAINING: None,
    ATTR_USAGE_PERCENTAGE: None,
    ATTR_UNLIMITED: True,
    ATTR_USAGE_GBR: 5.0,
    ATTR_TOTAL_GBR: 0.0,
}

# A SIM whose wallet has never been topped up.
MOCK_COORDINATOR_DATA_NO_WALLET: dict[str, Any] = {
    **MOCK_COORDINATOR_DATA,
    ATTR_WALLET: MOCK_WALLET_INACTIVE,
    ATTR_WALLET_BALANCE: 0.0,
    ATTR_WALLET_SPEND: 0.0,
    ATTR_OVERSPEND_LIMIT_REACHED: True,
}


def _make_config_entry(
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    entry_id: str = "test_entry_id",
    version: int = 3,
    subentries: list[dict[str, Any]] | None = None,
) -> MockConfigEntry:
    """Create a mock account entry with one SIM subentry by default."""

    entry_data = data if data is not None else MOCK_ENTRY_DATA_COOKIE
    return MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        options=options or {},
        entry_id=entry_id,
        version=version,
        unique_id=entry_data.get(CONF_EMAIL) or "account",
        subentries_data=(
            subentries if subentries is not None else [_sim_subentry_data()]
        ),
    )


def sim_subentry(entry: ConfigEntry) -> ConfigSubentry:
    """Return the entry's first SIM subentry."""

    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_SIM
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry using cookie auth."""
    return _make_config_entry(data=MOCK_ENTRY_DATA_COOKIE)


@pytest.fixture
def mock_config_entry_login() -> MockConfigEntry:
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
    client.async_fetch_sims.return_value = [MOCK_SIM]
    client.async_fetch_sim.return_value = MOCK_SIM
    client.async_fetch_overspend.return_value = MOCK_WALLET
    return client


@pytest.fixture
def mock_coordinator(mock_config_entry) -> MagicMock:
    """Return a mock MozillionCoordinator with data pre-loaded."""
    coordinator = MagicMock()
    coordinator.data = MOCK_COORDINATOR_DATA
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator
