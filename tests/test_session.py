"""Tests for the account session shared by every SIM.

One login covers the whole account, so this is what stops N SIMs from logging in N
times and racing to persist the same cookie back to the entry.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from custom_components.mozillion.const import AUTH_REFRESH_THRESHOLD
from custom_components.mozillion.session import MozillionSession

from tests.conftest import (
    MOCK_ENTRY_DATA_COOKIE,
    MOCK_ENTRY_DATA_LOGIN,
    _make_config_entry,
)

# Not marked async globally: asyncio_mode is 'auto' in pyproject, and this
# module mixes sync and async tests.


class TestFromEntry:
    """Building the session from what the entry already stores."""

    def test_takes_the_stored_session(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_COOKIE)
        session = MozillionSession.from_entry(entry)

        assert session.cookie_header == "mozillion_session=abc; XSRF-TOKEN=xyz"
        assert session.xsrf_token == "xyz"
        # Unknown until this integration authenticates, which forces a refresh.
        assert session.authenticated_at is None

    def test_empty_cookie_becomes_none(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)
        assert MozillionSession.from_entry(entry).cookie_header is None


class TestNeedsAuthentication:
    """The refresh heuristic, which now decides for the whole account."""

    def test_cookie_only_entry_never_logs_in(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_COOKIE)

        assert MozillionSession.from_entry(entry).needs_authentication(entry) is False

    def test_credentials_without_a_session_log_in(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)

        assert MozillionSession().needs_authentication(entry) is True

    def test_fresh_session_is_reused(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)
        session = MozillionSession(cookie_header="c", authenticated_at=time.monotonic())

        assert session.needs_authentication(entry) is False

    def test_stale_session_is_refreshed(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)
        session = MozillionSession(
            cookie_header="c",
            authenticated_at=time.monotonic() - (AUTH_REFRESH_THRESHOLD + 1),
        )

        assert session.needs_authentication(entry) is True


class TestAuthenticate:
    """Logging in, and telling the caller whether it did."""

    async def test_no_credentials_is_a_no_op(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_COOKIE)
        client = AsyncMock()
        session = MozillionSession()

        assert await session.async_authenticate(client, entry) is False
        client.async_login.assert_not_awaited()

    async def test_logs_in_and_reports_that_it_did(self) -> None:
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)
        client = AsyncMock()
        client.async_login.return_value = ("fresh-cookie", "fresh-xsrf")
        session = MozillionSession()

        assert await session.async_authenticate(client, entry) is True
        assert session.cookie_header == "fresh-cookie"
        assert session.xsrf_token == "fresh-xsrf"
        assert session.authenticated_at is not None

    async def test_second_sim_reuses_the_session(self) -> None:
        """One login per account, however many SIMs are polling."""
        entry = _make_config_entry(data=MOCK_ENTRY_DATA_LOGIN)
        client = AsyncMock()
        client.async_login.return_value = ("fresh", "xsrf")
        session = MozillionSession()

        assert await session.async_authenticate(client, entry) is True
        assert await session.async_authenticate(client, entry) is False
        client.async_login.assert_awaited_once()


class TestAdopt:
    """Taking over a session established elsewhere, such as by a config flow."""

    def test_adopts_the_cookies(self) -> None:
        session = MozillionSession()
        session.adopt("cookie", "xsrf")

        assert session.cookie_header == "cookie"
        assert session.xsrf_token == "xsrf"
        assert session.authenticated_at is not None
