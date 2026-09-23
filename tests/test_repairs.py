"""Tests for the rate-limit handling and the dashboard repair.

Both exist because of the same underlying problem: when something outside this
integration changes, the user should be told rather than left with silently
unavailable entities.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError, ClientSession
from custom_components.mozillion import MozillionAuthError
from custom_components.mozillion.api import MozillionClient
from custom_components.mozillion.const import (
    ATTR_SIM_STATUS,
    ATTR_USAGE,
    DOMAIN,
    ISSUE_DASHBOARD_UNREADABLE,
    REPAIR_FAILURE_THRESHOLD,
)
from custom_components.mozillion.coordinator import MozillionCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_ENTRY_DATA_COOKIE,
    MOCK_SIM,
    MOCK_WALLET,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def _rate_limited(headers: dict[str, str] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status = 429
    resp.url = "https://www.mozillion.com/get-data-usage"
    resp.headers = {"Content-Type": "application/json", **(headers or {})}
    resp.raise_for_status = MagicMock()
    return resp


def _ctx(resp: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestRateLimit:
    """A 429 must say what to do about it."""

    async def test_429_reports_the_retry_window(self) -> None:
        session = MagicMock(spec=ClientSession)
        session.get = MagicMock(
            side_effect=[_ctx(_rate_limited({"Retry-After": "42"}))]
        )

        client = MozillionClient(session)
        with pytest.raises(RuntimeError) as err:
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

        message = str(err.value)
        assert "429" in message
        assert "42s" in message
        assert "scan interval" in message

    async def test_429_without_retry_after_still_explains(self) -> None:
        session = MagicMock(spec=ClientSession)
        session.get = MagicMock(side_effect=[_ctx(_rate_limited())])

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="rate-limited"):
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

    async def test_429_is_not_mistaken_for_an_expired_session(self) -> None:
        """A rate limit is transient; it must not send the user to reauth."""
        session = MagicMock(spec=ClientSession)
        session.get = MagicMock(side_effect=[_ctx(_rate_limited())])

        client = MozillionClient(session)
        with pytest.raises(RuntimeError) as err:
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

        assert not isinstance(err.value, MozillionAuthError)

    async def test_429_on_the_dashboard_read(self) -> None:
        session = MagicMock(spec=ClientSession)
        session.get = MagicMock(side_effect=[_ctx(_rate_limited())])

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="rate-limited"):
            await client.async_fetch_sims(cookie_header="mozillion_session=abc")


# ---------------------------------------------------------------------------
# Dashboard repair
# ---------------------------------------------------------------------------


def _issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_DASHBOARD_UNREADABLE)


def _coordinator(hass: HomeAssistant) -> tuple[MozillionCoordinator, AsyncMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion 07700900000",
        data=dict(MOCK_ENTRY_DATA_COOKIE),
        unique_id="7654321",
        version=2,
    )
    entry.add_to_hass(hass)

    client = AsyncMock()
    client.async_get_usage.return_value = MOCK_API_RESPONSE
    client.async_fetch_sim.return_value = MOCK_SIM
    client.async_fetch_overspend.return_value = MOCK_WALLET

    coordinator = MozillionCoordinator(
        hass=hass,
        client=client,
        entry=entry,
        cookie_header="mozillion_session=abc",
        xsrf_header="xyz",
        update_interval=None,
    )
    coordinator._reset_poll_state()
    return coordinator, client


class TestDashboardRepair:
    """Repeated dashboard failures must surface in the UI, not just the log."""

    async def test_no_repair_for_a_single_blip(self, hass: HomeAssistant) -> None:
        coordinator, client = _coordinator(hass)
        client.async_fetch_sim.side_effect = RuntimeError("markup changed")

        await coordinator._async_update_data()

        assert _issue(hass) is None

    async def test_repair_raised_after_repeated_failures(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _coordinator(hass)
        client.async_fetch_sim.side_effect = RuntimeError("markup changed")

        for _ in range(REPAIR_FAILURE_THRESHOLD):
            await coordinator._async_update_data()

        issue = _issue(hass)
        assert issue is not None
        assert issue.translation_key == ISSUE_DASHBOARD_UNREADABLE
        assert issue.translation_placeholders["attempts"] == str(
            REPAIR_FAILURE_THRESHOLD
        )
        assert "markup changed" in issue.translation_placeholders["error"]

    async def test_usage_survives_while_the_dashboard_is_broken(
        self, hass: HomeAssistant
    ) -> None:
        """The repair must not take data usage down with it."""
        coordinator, client = _coordinator(hass)
        client.async_fetch_sim.side_effect = RuntimeError("markup changed")

        for _ in range(REPAIR_FAILURE_THRESHOLD):
            result = await coordinator._async_update_data()

        assert result[ATTR_USAGE] == 3.5
        assert result[ATTR_SIM_STATUS] == ""

    async def test_repair_clears_when_the_dashboard_recovers(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _coordinator(hass)
        client.async_fetch_sim.side_effect = RuntimeError("markup changed")
        for _ in range(REPAIR_FAILURE_THRESHOLD):
            await coordinator._async_update_data()
        assert _issue(hass) is not None

        client.async_fetch_sim.side_effect = None
        client.async_fetch_sim.return_value = MOCK_SIM
        result = await coordinator._async_update_data()

        assert _issue(hass) is None
        assert result[ATTR_SIM_STATUS] == "ACTIVE"
        assert coordinator._dashboard_failures == 0

    async def test_auth_errors_do_not_count_as_dashboard_failures(
        self, hass: HomeAssistant
    ) -> None:
        """An expired session needs reauth, not a 'markup changed' repair."""
        coordinator, client = _coordinator(hass)
        client.async_fetch_sim.side_effect = MozillionAuthError("login page")

        with pytest.raises(MozillionAuthError):
            await coordinator._async_read_dashboard()

        assert coordinator._dashboard_failures == 0
        assert _issue(hass) is None

    async def test_clientside_errors_also_count(self, hass: HomeAssistant) -> None:
        coordinator, client = _coordinator(hass)
        client.async_fetch_sim.side_effect = ClientError("connection reset")

        for _ in range(REPAIR_FAILURE_THRESHOLD):
            await coordinator._async_update_data()

        assert _issue(hass) is not None

    async def test_repair_keeps_the_attempt_count_current(
        self, hass: HomeAssistant
    ) -> None:
        """Past the threshold each poll updates the same issue rather than stacking."""
        coordinator, client = _coordinator(hass)
        client.async_fetch_sim.side_effect = RuntimeError("markup changed")

        with patch(
            "custom_components.mozillion.coordinator.ir.async_create_issue"
        ) as create:
            for _ in range(REPAIR_FAILURE_THRESHOLD + 2):
                await coordinator._async_update_data()

        # Called on each failing poll past the threshold, always with the same id,
        # which is what the registry expects.
        assert create.call_count == 3
        assert {call.args[2] for call in create.call_args_list} == {
            ISSUE_DASHBOARD_UNREADABLE
        }
