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

from .api import MozillionChallengeError, MozillionClient
from .const import (
    AUTH_REFRESH_THRESHOLD,
    CLEARANCE_REFRESH_MARGIN,
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
    # True for a cookie read from the entry that this run has not tried yet. It is
    # presumed good: a login is only for a session the server has rejected, because
    # the login is the one request Cloudflare challenges.
    unverified: bool = False
    # When the solver's Cloudflare clearance stops working (epoch seconds), so the
    # session can renew it before a poll is challenged. None when no solver is used.
    clearance_expires_at: float | None = None
    # Whether a Cloudflare repair is outstanding for the account. Shared, so
    # whichever SIM polls successfully first can withdraw it.
    challenge_issue_active: bool = False
    # Only one login may be in flight, however many SIMs poll at once.
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> MozillionSession:
        """Build the session from what the entry already stores."""

        cookie_header = entry.data.get(CONF_SESSION_COOKIE) or None
        return cls(
            cookie_header=cookie_header,
            xsrf_token=entry.data.get(CONF_XSRF_TOKEN),
            unverified=bool(cookie_header),
        )

    def invalidate(self) -> None:
        """Mark the session as rejected, so the next check logs in.

        Distinct from merely not having tried a stored cookie yet: this is the
        server saying no.
        """

        self.authenticated_at = None
        self.unverified = False

    def needs_authentication(self, entry: ConfigEntry) -> bool:
        """Return True when the session should be (re)authenticated now.

        A cookie-only entry cannot refresh itself and never tries; an entry with
        credentials logs in when the session is missing, rejected, or older than the
        threshold. A stored session this run has not tried yet is given the chance to
        work first, so a restart does not start with a login.
        """

        if not has_credentials(entry):
            return False
        if not self.cookie_header:
            return True
        if self.unverified:
            return False
        if self.authenticated_at is None:
            return True
        if (
            self.clearance_expires_at is not None
            and time.time() > self.clearance_expires_at - CLEARANCE_REFRESH_MARGIN
        ):
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

            # A refresh only because the session is getting old, with a cookie that
            # has not been rejected, is a precaution. A forced login is not.
            precaution = bool(self.cookie_header) and self.authenticated_at is not None

            _LOGGER.debug("Refreshing the Mozillion session")
            try:
                self.cookie_header, self.xsrf_token = await client.async_login(
                    email=entry.data[CONF_EMAIL],
                    password=entry.data[CONF_PASSWORD],
                    totp_secret=entry.data.get(CONF_TOTP_SECRET) or None,
                    origin=entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
                )
            except MozillionChallengeError as err:
                if not precaution:
                    raise
                # Cloudflare challenges the login, not the polls. The cookie we hold
                # has not been rejected, so keep using it and try again after another
                # full period rather than failing a poll that would have worked.
                _LOGGER.warning(
                    "Could not renew the Mozillion session (%s); keeping the "
                    "current one",
                    err,
                )
                self.authenticated_at = time.monotonic()
                return False
            self.authenticated_at = time.monotonic()
            self.unverified = False
            expires = client.clearance_expires_at
            self.clearance_expires_at = (
                float(expires) if isinstance(expires, int | float) else None
            )
            return True

    def adopt(self, cookie_header: str, xsrf_token: str | None) -> None:
        """Take over a session established elsewhere, such as by a config flow."""

        self.cookie_header = cookie_header
        self.xsrf_token = xsrf_token
        self.authenticated_at = time.monotonic()
        self.unverified = False


def has_credentials(entry: ConfigEntry) -> bool:
    """Return True when the entry can log in on its own."""

    return bool(entry.data.get(CONF_EMAIL) and entry.data.get(CONF_PASSWORD))
