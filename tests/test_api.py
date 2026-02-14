"""Tests for the Mozillion API client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from aiohttp import ClientError, ClientSession

from custom_components.mozillion.api import (
    MozillionClient,
    _extract_csrf,
    _build_cookie_header,
)


# ---------------------------------------------------------------------------
# _extract_csrf
# ---------------------------------------------------------------------------

class TestExtractCsrf:
    """Tests for the _extract_csrf helper."""

    def test_extracts_from_input_field(self) -> None:
        html = '<input type="hidden" name="_token" value="abc123">'
        assert _extract_csrf(html) == "abc123"

    def test_extracts_from_meta_tag(self) -> None:
        html = '<meta name="csrf-token" content="meta-token-value">'
        assert _extract_csrf(html) == "meta-token-value"

    def test_prefers_input_over_meta(self) -> None:
        html = (
            '<input name="_token" value="input-tok">'
            '<meta name="csrf-token" content="meta-tok">'
        )
        assert _extract_csrf(html) == "input-tok"

    def test_returns_none_when_absent(self) -> None:
        assert _extract_csrf("<html><body>No token</body></html>") is None

    def test_returns_none_for_empty_string(self) -> None:
        assert _extract_csrf("") is None


# ---------------------------------------------------------------------------
# _build_cookie_header
# ---------------------------------------------------------------------------

class TestBuildCookieHeader:
    """Tests for the _build_cookie_header helper."""

    def test_builds_header_from_cookie_jar(self) -> None:
        session = MagicMock(spec=ClientSession)
        cookie1 = MagicMock()
        cookie1.key = "mozillion_session"
        cookie1.value = "sess-val"
        cookie2 = MagicMock()
        cookie2.key = "XSRF-TOKEN"
        cookie2.value = "xsrf%3Dval"

        session.cookie_jar = [cookie1, cookie2]

        header, xsrf = _build_cookie_header(session)
        assert "mozillion_session=sess-val" in header
        assert "XSRF-TOKEN=xsrf%3Dval" in header
        # XSRF should be URL-decoded
        assert xsrf == "xsrf=val"

    def test_no_xsrf_cookie(self) -> None:
        session = MagicMock(spec=ClientSession)
        cookie1 = MagicMock()
        cookie1.key = "mozillion_session"
        cookie1.value = "sess-val"
        session.cookie_jar = [cookie1]

        header, xsrf = _build_cookie_header(session)
        assert header == "mozillion_session=sess-val"
        assert xsrf is None

    def test_empty_jar(self) -> None:
        session = MagicMock(spec=ClientSession)
        session.cookie_jar = []

        header, xsrf = _build_cookie_header(session)
        assert header == ""
        assert xsrf is None


# ---------------------------------------------------------------------------
# MozillionClient.async_login
# ---------------------------------------------------------------------------

class TestAsyncLogin:
    """Tests for the login flow."""

    @pytest.mark.asyncio
    async def test_login_success(self) -> None:
        """Successful login returns cookie header and xsrf."""
        session = AsyncMock(spec=ClientSession)

        # Mock the GET login page
        login_resp = AsyncMock()
        login_resp.raise_for_status = MagicMock()
        login_resp.text = AsyncMock(
            return_value='<input name="_token" value="csrf123">'
        )

        # Mock the POST login
        post_resp = AsyncMock()
        post_resp.raise_for_status = MagicMock()
        post_resp.status = 200
        post_resp.text = AsyncMock(return_value="<html>Dashboard</html>")

        # Context managers
        session.get.return_value.__aenter__ = AsyncMock(return_value=login_resp)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)
        session.post.return_value.__aenter__ = AsyncMock(return_value=post_resp)
        session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "custom_components.mozillion.api._build_cookie_header",
            return_value=("session=abc", "xsrf-decoded"),
        ):
            client = MozillionClient(session)
            cookie, xsrf = await client.async_login("user@example.com", "pass123")

        assert cookie == "session=abc"
        assert xsrf == "xsrf-decoded"

    @pytest.mark.asyncio
    async def test_login_no_csrf_raises(self) -> None:
        """Login raises when no CSRF token found on login page."""
        session = AsyncMock(spec=ClientSession)
        login_resp = AsyncMock()
        login_resp.raise_for_status = MagicMock()
        login_resp.text = AsyncMock(return_value="<html>No token</html>")

        session.get.return_value.__aenter__ = AsyncMock(return_value=login_resp)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="CSRF token"):
            await client.async_login("user@example.com", "pass123")

    @pytest.mark.asyncio
    async def test_login_no_cookies_raises(self) -> None:
        """Login raises when no cookies returned after auth."""
        session = AsyncMock(spec=ClientSession)

        login_resp = AsyncMock()
        login_resp.raise_for_status = MagicMock()
        login_resp.text = AsyncMock(
            return_value='<input name="_token" value="csrf123">'
        )

        post_resp = AsyncMock()
        post_resp.raise_for_status = MagicMock()
        post_resp.status = 200
        post_resp.text = AsyncMock(return_value="<html></html>")

        session.get.return_value.__aenter__ = AsyncMock(return_value=login_resp)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)
        session.post.return_value.__aenter__ = AsyncMock(return_value=post_resp)
        session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "custom_components.mozillion.api._build_cookie_header",
            return_value=("", None),
        ):
            client = MozillionClient(session)
            with pytest.raises(RuntimeError, match="No cookies"):
                await client.async_login("user@example.com", "pass123")

    @pytest.mark.asyncio
    async def test_login_client_error_raises(self) -> None:
        """Login raises on network error."""
        session = AsyncMock(spec=ClientSession)

        login_resp = AsyncMock()
        login_resp.raise_for_status = MagicMock(side_effect=ClientError("timeout"))

        session.get.return_value.__aenter__ = AsyncMock(return_value=login_resp)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="Error fetching login page"):
            await client.async_login("user@example.com", "pass123")


# ---------------------------------------------------------------------------
# MozillionClient.async_get_usage
# ---------------------------------------------------------------------------

class TestAsyncGetUsage:
    """Tests for the usage data fetch."""

    @pytest.mark.asyncio
    async def test_get_usage_success(self) -> None:
        """Successful usage fetch returns JSON data."""
        session = AsyncMock(spec=ClientSession)
        usage_data = {"usedData": 3.5, "totalData": 10.0}

        trigger_resp = AsyncMock()
        trigger_resp.raise_for_status = MagicMock()
        trigger_resp.json = AsyncMock(return_value={"status": "ok"})

        status_resp = AsyncMock()
        status_resp.raise_for_status = MagicMock()
        status_resp.json = AsyncMock(return_value=usage_data)

        # get is called twice: trigger, then status
        session.get.return_value.__aenter__ = AsyncMock(
            side_effect=[trigger_resp, status_resp]
        )
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MozillionClient(session)
        result = await client.async_get_usage(
            order_detail_id="order-1",
            sim_plan_id="sim-1",
            cookie_header="session=abc",
            xsrf_token="tok",
        )
        assert result == usage_data

    @pytest.mark.asyncio
    async def test_get_usage_network_error(self) -> None:
        """Usage fetch raises on network error."""
        session = AsyncMock(spec=ClientSession)

        trigger_resp = AsyncMock()
        trigger_resp.raise_for_status = MagicMock(side_effect=ClientError("fail"))

        session.get.return_value.__aenter__ = AsyncMock(return_value=trigger_resp)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="Error triggering"):
            await client.async_get_usage(
                order_detail_id="order-1",
                sim_plan_id="sim-1",
                cookie_header="session=abc",
            )

    @pytest.mark.asyncio
    async def test_get_usage_non_json_response(self) -> None:
        """Usage fetch raises when response is not JSON."""
        session = AsyncMock(spec=ClientSession)

        trigger_resp = AsyncMock()
        trigger_resp.raise_for_status = MagicMock()
        trigger_resp.json = AsyncMock(side_effect=ValueError("not json"))

        session.get.return_value.__aenter__ = AsyncMock(return_value=trigger_resp)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="not JSON"):
            await client.async_get_usage(
                order_detail_id="order-1",
                sim_plan_id="sim-1",
                cookie_header="session=abc",
            )
