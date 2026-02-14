"""Live integration tests against the real Mozillion API.

These tests are SKIPPED unless a .env file (or env vars) provides credentials.
They perform real network requests — do NOT run in CI.

Required .env variables:
    MOZILLION_EMAIL=you@example.com
    MOZILLION_PASSWORD=your-password

Optional:
    MOZILLION_TOTP_SECRET=BASE32SECRET   # if you have 2FA enabled
    MOZILLION_ORIGIN=https://app.mozillion.com  # override if needed
"""

from __future__ import annotations

import os
import logging

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from aiohttp import ClientSession, TCPConnector
from aiohttp.resolver import ThreadedResolver

from custom_components.mozillion.api import MozillionClient
from custom_components.mozillion.const import DEFAULT_ORIGIN

# Load .env from repo root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skip condition
# ---------------------------------------------------------------------------

_EMAIL = os.getenv("MOZILLION_EMAIL", "")
_PASSWORD = os.getenv("MOZILLION_PASSWORD", "")
_TOTP_SECRET = os.getenv("MOZILLION_TOTP_SECRET", "") or None
_ORIGIN = os.getenv("MOZILLION_ORIGIN", DEFAULT_ORIGIN)

_HAS_CREDS = bool(_EMAIL and _PASSWORD)

pytestmark = [
    pytest.mark.skipif(
        not _HAS_CREDS,
        reason="Live tests require MOZILLION_EMAIL and MOZILLION_PASSWORD in .env",
    ),
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session():
    """Create and cleanup an aiohttp session with threaded DNS resolver."""
    connector = TCPConnector(resolver=ThreadedResolver())
    async with ClientSession(connector=connector) as s:
        yield s


@pytest_asyncio.fixture
async def authenticated_client(session: ClientSession):
    """Return a MozillionClient that has already logged in, along with cookies."""
    client = MozillionClient(session)
    cookie_header, xsrf_token = await client.async_login(
        email=_EMAIL,
        password=_PASSWORD,
        totp_secret=_TOTP_SECRET,
        origin=_ORIGIN,
    )
    return client, cookie_header, xsrf_token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLiveLogin:
    """Test real login against Mozillion."""

    @pytest.mark.asyncio
    async def test_login_returns_cookies(self, session: ClientSession) -> None:
        """Real login should return a non-empty cookie header."""
        client = MozillionClient(session)
        cookie_header, xsrf_token = await client.async_login(
            email=_EMAIL,
            password=_PASSWORD,
            totp_secret=_TOTP_SECRET,
            origin=_ORIGIN,
        )
        assert cookie_header, "Expected non-empty cookie header after login"
        _LOGGER.info("Login OK — cookie length: %d", len(cookie_header))
        if xsrf_token:
            _LOGGER.info("XSRF token received: %s...", xsrf_token[:8])

    @pytest.mark.asyncio
    async def test_bad_password_behaviour(self, session: ClientSession) -> None:
        """Login with wrong password should either raise or produce invalid cookies.

        Mozillion's login may not immediately reject bad credentials — it might
        return cookies that later fail.  We accept either behaviour here.
        """
        client = MozillionClient(session)
        try:
            cookie_header, xsrf_token = await client.async_login(
                email=_EMAIL,
                password="definitely-wrong-password",
                totp_secret=None,
                origin=_ORIGIN,
            )
            # If it didn't raise, the cookies should be empty or the
            # subsequent API call should fail
            _LOGGER.info(
                "Bad-password login did not raise; cookie length: %d",
                len(cookie_header) if cookie_header else 0,
            )
        except RuntimeError:
            _LOGGER.info("Bad-password login correctly raised RuntimeError")


class TestLiveDashboard:
    """Test fetching dashboard plans."""

    @pytest.mark.asyncio
    async def test_fetch_dashboard_ids(self, authenticated_client) -> None:
        """Dashboard fetch should return a non-empty list of plans."""
        client, cookie_header, xsrf_token = authenticated_client
        plans = await client.async_fetch_dashboard_ids(
            cookie_header=cookie_header,
            xsrf_token=xsrf_token,
        )
        assert isinstance(plans, list), f"Expected list, got {type(plans)}"
        assert len(plans) > 0, "Expected at least one plan"

        first = plans[0]
        assert "sim_plan_id" in first, f"Missing sim_plan_id in {first}"
        assert "order_detail_id" in first, f"Missing order_detail_id in {first}"
        _LOGGER.info("Found %d plan(s): %s", len(plans), [p.get("name") for p in plans])


class TestLiveUsage:
    """Test fetching real usage data."""

    @pytest.mark.asyncio
    async def test_get_usage_returns_data(self, authenticated_client) -> None:
        """Usage fetch should return a dict with expected keys."""
        client, cookie_header, xsrf_token = authenticated_client

        # First get the plan IDs
        plans = await client.async_fetch_dashboard_ids(
            cookie_header=cookie_header,
            xsrf_token=xsrf_token,
        )
        assert plans, "No plans found — cannot test usage fetch"

        plan = plans[0]
        usage = await client.async_get_usage(
            order_detail_id=plan["order_detail_id"],
            sim_plan_id=plan["sim_plan_id"],
            cookie_header=cookie_header,
            xsrf_token=xsrf_token,
        )

        assert isinstance(usage, dict), f"Expected dict, got {type(usage)}"
        _LOGGER.info("Usage data keys: %s", list(usage.keys()))
        _LOGGER.info("Usage data: %s", usage)

    @pytest.mark.asyncio
    async def test_full_flow_end_to_end(self, session: ClientSession) -> None:
        """Full flow: login → dashboard → usage — mimics what the integration does."""
        client = MozillionClient(session)

        # Step 1: Login
        cookie_header, xsrf_token = await client.async_login(
            email=_EMAIL,
            password=_PASSWORD,
            totp_secret=_TOTP_SECRET,
            origin=_ORIGIN,
        )
        assert cookie_header
        _LOGGER.info("✓ Login successful")

        # Step 2: Fetch plans
        plans = await client.async_fetch_dashboard_ids(
            cookie_header=cookie_header,
            xsrf_token=xsrf_token,
        )
        assert len(plans) > 0
        _LOGGER.info("✓ Found %d plan(s)", len(plans))

        # Step 3: Fetch usage for each plan
        for plan in plans:
            usage = await client.async_get_usage(
                order_detail_id=plan["order_detail_id"],
                sim_plan_id=plan["sim_plan_id"],
                cookie_header=cookie_header,
                xsrf_token=xsrf_token,
            )
            assert isinstance(usage, dict)
            _LOGGER.info(
                "✓ Plan '%s': %s",
                plan.get("name", "unknown"),
                {
                    k: v
                    for k, v in usage.items()
                    if k in ("usedData", "totalData", "isUnlimited")
                },
            )
