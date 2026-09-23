"""The account session, shared by every SIM coordinator.

One Mozillion login covers every SIM on the account, so the hub entry holds one of
these. Without it each SIM's coordinator would keep its own cookie snapshot, log in
separately, and race to persist the session back to the config entry -- N logins where
one will do, and a last-writer-wins scramble over the stored cookie.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry

from .api import MozillionClient
from .const import (
    AUTH_REFRESH_THRESHOLD,
    CONF_EMAIL,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class MozillionSession:
    """The cookies every SIM shares, refreshed once per account."""

    cookie_header: str | None = None
    xsrf_token: str | None = None
    # Monotonic time of the last successful authentication, used to decide when to
    # proactively refresh an expiring session. None (unknown) forces a refresh when
    # credentials are present.
    authenticated_at: float | None = None
    # Only one login may be in flight, however many SIMs poll at once.
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> MozillionSession:
        """Build the session from what the entry already stores."""

        return cls(
            cookie_header=entry.data.get(CONF_SESSION_COOKIE) or None,
            xsrf_token=entry.data.get(CONF_XSRF_TOKEN),
        )

    def needs_authentication(self, entry: ConfigEntry) -> bool:
        """Return True when the session should be (re)authenticated now.

        A cookie-only entry cannot refresh itself and never tries; an entry with
        credentials logs in when the session is missing or older than the threshold.
        """

        if not has_credentials(entry):
            return False
        if not self.cookie_header or self.authenticated_at is None:
            return True
        return (time.monotonic() - self.authenticated_at) > AUTH_REFRESH_THRESHOLD

    async def async_authenticate(
        self, client: MozillionClient, entry: ConfigEntry
    ) -> bool:
        """Log in if needed and share the cookies with every SIM.

        Returns True when this call performed the login, so the caller can persist
        the refreshed session exactly once rather than once per SIM.
        """

        if not has_credentials(entry):
            return False

        async with self._lock:
            # Another coordinator may have logged in while we waited for the lock.
            if not self.needs_authentication(entry):
                return False

            _LOGGER.debug("Refreshing the Mozillion session")
            self.cookie_header, self.xsrf_token = await client.async_login(
                email=entry.data[CONF_EMAIL],
                password=entry.data[CONF_PASSWORD],
                totp_secret=entry.data.get(CONF_TOTP_SECRET) or None,
                origin=entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
            )
            self.authenticated_at = time.monotonic()
            return True

    def adopt(self, cookie_header: str, xsrf_token: str | None) -> None:
        """Take over a session established elsewhere, such as by a config flow."""

        self.cookie_header = cookie_header
        self.xsrf_token = xsrf_token
        self.authenticated_at = time.monotonic()


def has_credentials(entry: ConfigEntry) -> bool:
    """Return True when the entry can log in on its own."""

    return bool(entry.data.get(CONF_EMAIL) and entry.data.get(CONF_PASSWORD))
