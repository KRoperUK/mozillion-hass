"""Tests for the Mozillion API client."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError, ClientSession, CookieJar
from custom_components.mozillion.api import (
    MozillionAuthError,
    MozillionClient,
    _build_cookie_header,
    _extract_csrf,
    parse_sims,
)
from custom_components.mozillion.const import (
    BASE_URL,
    DASHBOARD_PATH,
    DATA_USAGE_STATUS_PATH,
    USAGE_POLL_ATTEMPTS,
)
from yarl import URL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Trimmed from a real capture of /new-user-dashboard, keeping the multi-line
# attribute layout so the parser is exercised against the real shape.
DASHBOARD_HTML = """
<html><body>
<div id="sim-dropdown-menu">
    <button type="button" class="sim-option-check inline-flex h-5 w-5 shrink-0"
        data-sim-id="99999" data-orderdetail-id="00000">decoy</button>
    <button type="button"
        class="sim-option group flex w-full items-center text-left hover:bg-[#f4f6f8]"
        data-sim-id="7654321" data-sim-number="07700900000"
        data-iccid="89440000000000000000"
        data-orderdetail-id="1234567"
        data-label="Your SIM" data-status="ACTIVE"
        data-billing-amount="999.00" data-billing-days=""
        data-usage-reset-label="19 Oct"
        data-has-bill="0"
        data-has-kyb="0"
        data-used-data="2.19" data-total-data="100"
        data-used-data-gbr="0" data-total-data-gbr="0"
        data-used-data-global="2.19" data-total-data-global="100"
        data-is-unlimited="0" data-days-left=""
        data-plan-duration="24-Months" data-plan-data-tariff="100GB"
        data-plan-texts-minutes="Unlimited calls and texts"
        data-plan-is-data-only="0"
        data-plan-roaming="EU roaming in 41 countries"
        aria-selected="true">
        <span>Your SIM</span>
    </button>
</div>
</body></html>
"""


def _response(
    *,
    text: str = "",
    status: int = 200,
    url: str = f"{BASE_URL}/",
    json_data: Any = None,
    content_type: str = "text/html",
) -> MagicMock:
    """Build a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.url = url
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status = MagicMock()
    resp.text = AsyncMock(return_value=text)
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    return resp


def _ctx(resp: MagicMock) -> MagicMock:
    """Wrap a mock response as an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _session(
    get_responses: tuple[MagicMock, ...] = (),
    post_responses: tuple[MagicMock, ...] = (),
    cookies: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock ClientSession returning the queued responses."""
    session = MagicMock(spec=ClientSession)
    session.get = MagicMock(side_effect=[_ctx(r) for r in get_responses])
    session.post = MagicMock(side_effect=[_ctx(r) for r in post_responses])
    session.cookie_jar = _cookie_jar(cookies or {})
    return session


def _cookie_jar(
    cookies: dict[str, str], domain: str = "www.mozillion.com"
) -> CookieJar:
    """Build a real CookieJar populated for Mozillion's domain."""
    jar = CookieJar()
    url = URL(f"https://{domain}/")
    for name, value in cookies.items():
        jar.update_cookies({name: value}, response_url=url)
    return jar


LOGIN_PAGE = '<html><input type="hidden" name="_token" value="csrf123"></html>'
TWO_FACTOR_PAGE = '<html><input type="hidden" name="_token" value="csrf456"></html>'


# ---------------------------------------------------------------------------
# _extract_csrf
# ---------------------------------------------------------------------------


class TestExtractCsrf:
    """Tests for the _extract_csrf helper."""

    def test_extracts_from_input_field(self) -> None:
        assert _extract_csrf(LOGIN_PAGE) == "csrf123"

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
    """Tests for the _build_cookie_header helper.

    These are async because aiohttp's CookieJar needs a running event loop.
    """

    async def test_builds_header_from_cookie_jar(self) -> None:
        session = _session(
            cookies={"mozillion_session": "sess-val", "XSRF-TOKEN": "xsrf%3Dval"}
        )
        header, xsrf = _build_cookie_header(session)
        assert "mozillion_session=sess-val" in header
        assert "XSRF-TOKEN=xsrf%3Dval" in header
        # XSRF should be URL-decoded
        assert xsrf == "xsrf=val"

    async def test_no_xsrf_cookie(self) -> None:
        session = _session(cookies={"mozillion_session": "sess-val"})
        header, xsrf = _build_cookie_header(session)
        assert header == "mozillion_session=sess-val"
        assert xsrf is None

    async def test_empty_jar(self) -> None:
        session = _session()
        header, xsrf = _build_cookie_header(session)
        assert header == ""
        assert xsrf is None

    async def test_ignores_cookies_from_other_domains(self) -> None:
        """HA shares one session, so unrelated cookies must not leak in."""
        session = _session()
        session.cookie_jar.update_cookies(
            {"unrelated": "nope"}, response_url=URL("https://example.com/")
        )
        session.cookie_jar.update_cookies(
            {"mozillion_session": "sess-val"}, response_url=URL(f"{BASE_URL}/")
        )

        header, _ = _build_cookie_header(session)

        assert "mozillion_session=sess-val" in header
        assert "unrelated" not in header


# ---------------------------------------------------------------------------
# parse_sims
# ---------------------------------------------------------------------------


class TestParseSims:
    """Tests for the dashboard SIM parser."""

    def test_parses_the_real_dashboard_markup(self) -> None:
        sims = parse_sims(DASHBOARD_HTML)

        assert len(sims) == 1
        sim = sims[0]
        assert sim.sim_meta_id == "7654321"
        assert sim.order_detail_id == "1234567"
        assert sim.sim_number == "07700900000"
        assert sim.iccid == "89440000000000000000"
        assert sim.status == "ACTIVE"
        assert sim.reset_label == "19 Oct"
        assert sim.days_left == ""
        assert sim.used_data == 2.19
        assert sim.total_data == 100.0
        assert sim.used_data_gbr == 0.0
        assert sim.total_data_gbr == 0.0
        assert sim.used_data_global == 2.19
        assert sim.total_data_global == 100.0
        assert sim.is_unlimited is False
        assert sim.plan_duration == "24-Months"
        assert sim.plan_data_tariff == "100GB"
        assert sim.plan_roaming == "EU roaming in 41 countries"
        assert sim.plan_is_data_only is False

    def test_decoy_button_is_ignored(self) -> None:
        """`sim-option-check` is not a SIM selector."""
        sims = parse_sims(DASHBOARD_HTML)
        assert [sim.sim_meta_id for sim in sims] == ["7654321"]

    def test_multiple_sims(self) -> None:
        html = (
            '<button class="sim-option" data-sim-id="1" '
            'data-orderdetail-id="11">a</button>'
            '<button class="sim-option" data-sim-id="2" '
            'data-orderdetail-id="22">b</button>'
        )
        sims = parse_sims(html)
        assert [sim.sim_meta_id for sim in sims] == ["1", "2"]

    def test_skips_options_without_ids(self) -> None:
        html = '<button class="sim-option" data-label="No ids">x</button>'
        assert parse_sims(html) == []

    def test_empty_html(self) -> None:
        assert parse_sims("") == []

    def test_blank_numerics_become_none(self) -> None:
        html = (
            '<button class="sim-option" data-sim-id="1" data-orderdetail-id="2" '
            'data-used-data="" data-total-data="">x</button>'
        )
        sim = parse_sims(html)[0]
        assert sim.used_data is None
        assert sim.total_data is None

    def test_non_numeric_is_none(self) -> None:
        html = (
            '<button class="sim-option" data-sim-id="1" data-orderdetail-id="2" '
            'data-used-data="n/a">x</button>'
        )
        assert parse_sims(html)[0].used_data is None

    def test_unlimited_flag(self) -> None:
        html = (
            '<button class="sim-option" data-sim-id="1" data-orderdetail-id="2" '
            'data-is-unlimited="1">x</button>'
        )
        assert parse_sims(html)[0].is_unlimited is True


class TestSimDisplayName:
    """Tests for MozillionSim.display_name."""

    def test_number_and_tariff(self) -> None:
        sim = parse_sims(
            '<button class="sim-option" data-sim-id="1" data-orderdetail-id="2" '
            'data-sim-number="07700900000" data-plan-data-tariff="100GB">x</button>'
        )[0]
        assert sim.display_name == "07700900000 (100GB)"

    def test_number_only(self) -> None:
        sim = parse_sims(
            '<button class="sim-option" data-sim-id="1" data-orderdetail-id="2" '
            'data-sim-number="07700900000">x</button>'
        )[0]
        assert sim.display_name == "07700900000"

    def test_falls_back_to_meta_id(self) -> None:
        sim = parse_sims(
            '<button class="sim-option" data-sim-id="7" '
            'data-orderdetail-id="2">x</button>'
        )[0]
        assert sim.display_name == "SIM 7"


# ---------------------------------------------------------------------------
# MozillionClient.async_login
# ---------------------------------------------------------------------------


class TestAsyncLogin:
    """Tests for the login flow."""

    @pytest.mark.asyncio
    async def test_login_success_without_2fa(self) -> None:
        """A login that is not challenged returns the session cookies."""
        session = _session(
            get_responses=(_response(text=LOGIN_PAGE),),
            post_responses=(
                _response(
                    text="<html>dashboard</html>", url=f"{BASE_URL}/user-dashboard"
                ),
            ),
            cookies={"mozillion_session": "abc", "XSRF-TOKEN": "xyz"},
        )

        client = MozillionClient(session)
        cookie, xsrf = await client.async_login("user@example.com", "pass123")

        assert cookie == "mozillion_session=abc; XSRF-TOKEN=xyz"
        assert xsrf == "xyz"
        assert session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_login_with_2fa_submits_totp(self) -> None:
        """A 2FA challenge is answered with a generated TOTP code."""
        session = _session(
            get_responses=(_response(text=LOGIN_PAGE),),
            post_responses=(
                _response(text=TWO_FACTOR_PAGE, url=f"{BASE_URL}/2fa/verify"),
                _response(text="<html>dash</html>", url=f"{BASE_URL}/user-dashboard"),
            ),
            cookies={"mozillion_session": "abc"},
        )

        with patch("custom_components.mozillion.api.pyotp.TOTP") as mock_totp:
            mock_totp.return_value.now.return_value = "123456"
            client = MozillionClient(session)
            await client.async_login(
                "user@example.com", "pass123", totp_secret="JBSWY3DPEHPK3PXP"
            )

        assert session.post.call_count == 2
        second_form = session.post.call_args_list[1].kwargs["data"]
        assert second_form["code"] == "123456"
        # The token must come from the 2FA page, not the original login page.
        assert second_form["_token"] == "csrf456"

    @pytest.mark.asyncio
    async def test_login_2fa_challenge_without_secret_raises(self) -> None:
        session = _session(
            get_responses=(_response(text=LOGIN_PAGE),),
            post_responses=(
                _response(text=TWO_FACTOR_PAGE, url=f"{BASE_URL}/2fa/verify"),
            ),
        )

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="2FA code"):
            await client.async_login("user@example.com", "pass123")

    @pytest.mark.asyncio
    async def test_login_2fa_rejected_raises_auth_error(self) -> None:
        session = _session(
            get_responses=(_response(text=LOGIN_PAGE),),
            post_responses=(
                _response(text=TWO_FACTOR_PAGE, url=f"{BASE_URL}/2fa/verify"),
                _response(text=TWO_FACTOR_PAGE, url=f"{BASE_URL}/2fa/verify"),
            ),
        )

        client = MozillionClient(session)
        with pytest.raises(MozillionAuthError, match="2FA"):
            await client.async_login(
                "user@example.com", "pass123", totp_secret="JBSWY3DPEHPK3PXP"
            )

    @pytest.mark.asyncio
    async def test_login_totp_configured_but_not_challenged(self) -> None:
        """No 2FA prompt means no 2FA request, even with a secret configured."""
        session = _session(
            get_responses=(_response(text=LOGIN_PAGE),),
            post_responses=(
                _response(text="<html>dash</html>", url=f"{BASE_URL}/user-dashboard"),
            ),
            cookies={"mozillion_session": "abc"},
        )

        client = MozillionClient(session)
        cookie, _ = await client.async_login(
            "user@example.com", "pass123", totp_secret="JBSWY3DPEHPK3PXP"
        )

        assert cookie
        assert session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_login_bounced_back_to_login_raises_auth_error(self) -> None:
        session = _session(
            get_responses=(_response(text=LOGIN_PAGE),),
            post_responses=(_response(text=LOGIN_PAGE, url=f"{BASE_URL}/login"),),
        )

        client = MozillionClient(session)
        with pytest.raises(MozillionAuthError, match="rejected"):
            await client.async_login("user@example.com", "wrong")

    @pytest.mark.asyncio
    async def test_login_no_csrf_raises(self) -> None:
        session = _session(get_responses=(_response(text="<html>No token</html>"),))

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="CSRF token"):
            await client.async_login("user@example.com", "pass123")

    @pytest.mark.asyncio
    async def test_login_no_cookies_raises(self) -> None:
        session = _session(
            get_responses=(_response(text=LOGIN_PAGE),),
            post_responses=(
                _response(text="<html></html>", url=f"{BASE_URL}/user-dashboard"),
            ),
        )

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="No cookies"):
            await client.async_login("user@example.com", "pass123")

    @pytest.mark.asyncio
    async def test_login_client_error_raises(self) -> None:
        login_resp = _response(text=LOGIN_PAGE)
        login_resp.raise_for_status = MagicMock(side_effect=ClientError("timeout"))
        session = _session(get_responses=(login_resp,))

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="Error fetching login page"):
            await client.async_login("user@example.com", "pass123")


# ---------------------------------------------------------------------------
# MozillionClient.async_fetch_sims
# ---------------------------------------------------------------------------


class TestAsyncFetchSims:
    """Tests for the dashboard fetch."""

    @pytest.mark.asyncio
    async def test_returns_parsed_sims(self) -> None:
        session = _session(get_responses=(_response(text=DASHBOARD_HTML),))

        client = MozillionClient(session)
        sims = await client.async_fetch_sims(cookie_header="mozillion_session=abc")

        assert [sim.sim_meta_id for sim in sims] == ["7654321"]
        assert session.get.call_args.kwargs["headers"]["Cookie"] == (
            "mozillion_session=abc"
        )
        assert DASHBOARD_PATH in session.get.call_args.args[0]

    @pytest.mark.asyncio
    async def test_login_page_raises_auth_error(self) -> None:
        """A bounced session must not look like 'the markup changed'."""
        html = f'<form action="{BASE_URL}/login-post"><input name="password"></form>'
        session = _session(get_responses=(_response(text=html),))

        client = MozillionClient(session)
        with pytest.raises(MozillionAuthError, match="login page"):
            await client.async_fetch_sims(cookie_header="stale=1")

    @pytest.mark.asyncio
    async def test_missing_markup_raises_runtime_error(self) -> None:
        session = _session(
            get_responses=(_response(text="<html><body>empty</body></html>"),)
        )

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="No SIMs found"):
            await client.async_fetch_sims(cookie_header="mozillion_session=abc")


# ---------------------------------------------------------------------------
# MozillionClient.async_get_usage
# ---------------------------------------------------------------------------


class TestAsyncGetUsage:
    """Tests for the usage data fetch."""

    @pytest.mark.asyncio
    async def test_success_on_trigger(self) -> None:
        """A payload that is already complete needs no polling."""
        payload = {"status": "success", "usedData": 3.5, "totalData": 10.0}
        session = _session(
            get_responses=(
                _response(json_data=payload, content_type="application/json"),
            )
        )

        client = MozillionClient(session)
        result = await client.async_get_usage(
            order_detail_id="1234567",
            sim_meta_id="7654321",
            cookie_header="mozillion_session=abc",
        )

        assert result == payload
        assert session.get.call_count == 1
        params = session.get.call_args.kwargs["params"]
        assert params == {"order_detail_id": "1234567", "sim_meta_id": "7654321"}

    @pytest.mark.asyncio
    async def test_pending_then_success_polls_status_endpoint(self) -> None:
        """A pending trigger is polled until the figures are ready."""
        payload = {"status": "success", "usedData": 2.19, "totalData": 100}
        session = _session(
            get_responses=(
                _response(
                    json_data={"status": "pending"}, content_type="application/json"
                ),
                _response(
                    json_data={"status": "pending"}, content_type="application/json"
                ),
                _response(json_data=payload, content_type="application/json"),
            )
        )

        with patch(
            "custom_components.mozillion.api.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            client = MozillionClient(session)
            result = await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

        assert result == payload
        assert session.get.call_count == 3
        status_url = session.get.call_args_list[-1].args[0]
        assert status_url == f"{BASE_URL}{DATA_USAGE_STATUS_PATH}/7654321"
        # Exactly one sleep between the two pending answers.
        assert mock_sleep.await_count == 1

    @pytest.mark.asyncio
    async def test_never_ready_raises(self) -> None:
        """Polling is bounded."""
        session = _session(
            get_responses=tuple(
                _response(
                    json_data={"status": "pending"}, content_type="application/json"
                )
                for _ in range(USAGE_POLL_ATTEMPTS + 1)
            )
        )

        with patch(
            "custom_components.mozillion.api.asyncio.sleep", new_callable=AsyncMock
        ):
            client = MozillionClient(session)
            with pytest.raises(RuntimeError, match="did not finish"):
                await client.async_get_usage(
                    order_detail_id="1234567",
                    sim_meta_id="7654321",
                    cookie_header="mozillion_session=abc",
                )

        assert session.get.call_count == USAGE_POLL_ATTEMPTS + 1

    @pytest.mark.asyncio
    async def test_error_status_raises_with_message(self) -> None:
        session = _session(
            get_responses=(
                _response(
                    json_data={"status": "error", "message": "SIM not found"},
                    content_type="application/json",
                ),
            )
        )

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="SIM not found"):
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

    @pytest.mark.asyncio
    async def test_html_login_page_raises_auth_error(self) -> None:
        session = _session(
            get_responses=(
                _response(text="<html>login</html>", content_type="text/html"),
            )
        )

        client = MozillionClient(session)
        with pytest.raises(MozillionAuthError):
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        resp = _response(status=401, content_type="application/json")
        resp.raise_for_status = MagicMock(side_effect=ClientError("unauthorized"))
        session = _session(get_responses=(resp,))

        client = MozillionClient(session)
        with pytest.raises(MozillionAuthError, match="401"):
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

    @pytest.mark.asyncio
    async def test_network_error_raises(self) -> None:
        resp = _response(content_type="application/json")
        resp.raise_for_status = MagicMock(side_effect=ClientError("boom"))
        session = _session(get_responses=(resp,))

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="Error communicating"):
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )

    @pytest.mark.asyncio
    async def test_non_json_response_raises(self) -> None:
        resp = _response(content_type="application/json")
        resp.json = AsyncMock(side_effect=ValueError("not json"))
        session = _session(get_responses=(resp,))

        client = MozillionClient(session)
        with pytest.raises(RuntimeError, match="non-JSON"):
            await client.async_get_usage(
                order_detail_id="1234567",
                sim_meta_id="7654321",
                cookie_header="mozillion_session=abc",
            )
