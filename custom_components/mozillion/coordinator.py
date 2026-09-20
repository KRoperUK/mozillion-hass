"""Data update coordinator for Mozillion polling."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MozillionAuthError, MozillionClient
from .const import (
    ATTR_ICCID,
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_SIM_NUMBER,
    ATTR_TOTAL,
    ATTR_TOTAL_GBR,
    ATTR_TOTAL_GLOBAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_GBR,
    ATTR_USAGE_GLOBAL,
    ATTR_USAGE_PERCENTAGE,
    AUTH_REFRESH_THRESHOLD,
    CONF_EMAIL,
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
)

_LOGGER = logging.getLogger(__name__)

CoordinatorData = dict[str, Any]


def _to_float(value: Any) -> float | None:
    """Coerce an API value to float, treating blanks/None as unknown."""

    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MozillionCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator to poll the Mozillion usage endpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MozillionClient,
        entry: ConfigEntry,
        cookie_header: str | None,
        xsrf_header: str | None,
        update_interval: timedelta,
    ) -> None:
        self.client = client
        self.cookie_header = cookie_header
        self.xsrf_header = xsrf_header
        self.email = entry.data.get(CONF_EMAIL)
        self.password = entry.data.get(CONF_PASSWORD)
        self.totp_secret = entry.data.get(CONF_TOTP_SECRET) or None
        self.origin = entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN)
        # Monotonic time of the last successful authentication, used to decide
        # when to proactively refresh an expiring session. Unknown (None) until
        # we authenticate, which forces a refresh when credentials are present.
        self._auth_time: float | None = None

        super().__init__(
            hass,
            _LOGGER,
            name="Mozillion Data",
            update_interval=update_interval,
            # async_config_entry_first_refresh refuses to run for a coordinator
            # that does not know which config entry owns it.
            config_entry=entry,
        )

    def _needs_auth(self) -> bool:
        """Return True when the session should be (re)authenticated now."""

        # No session at all: only possible to obtain one if we have credentials.
        if not self.cookie_header:
            return bool(self.email and self.password)

        # Cookie-only configurations cannot refresh, so never attempt a login.
        if not (self.email and self.password):
            return False

        # Proactively re-authenticate once the session is older than the
        # threshold, so we never poll with an already-expired token.
        if self._auth_time is None:
            return True
        return (time.monotonic() - self._auth_time) > AUTH_REFRESH_THRESHOLD

    async def _async_refresh_auth(self) -> None:
        """Log in (if credentials are available) and store the new session."""

        if not (self.email and self.password):
            return

        _LOGGER.debug("Refreshing Mozillion session")
        self.cookie_header, self.xsrf_header = await self.client.async_login(
            email=self.email,
            password=self.password,
            totp_secret=self.totp_secret,
            origin=self.origin,
        )
        self._auth_time = time.monotonic()
        await self._async_persist_auth()

    async def _async_persist_auth(self) -> None:
        """Persist the refreshed session back to the config entry.

        This keeps the stored ``session_cookie``/``xsrf_token`` current so a
        reload or restart does not immediately start with a stale token.
        """

        if self.config_entry is None:
            return
        new_data = dict(self.config_entry.data)
        new_data[CONF_SESSION_COOKIE] = self.cookie_header or ""
        new_data[CONF_XSRF_TOKEN] = self.xsrf_header or ""
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)

    async def _async_fetch_usage(self) -> dict[str, Any]:
        """Call the usage endpoints with the current session."""

        return await self.client.async_get_usage(
            order_detail_id=self.config_entry.data[CONF_ORDER_DETAIL_ID],
            sim_meta_id=self.config_entry.data[CONF_SIM_META_ID],
            cookie_header=self.cookie_header or "",
            xsrf_token=self.xsrf_header,
        )

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch data from API, transparently re-authenticating on expiry."""

        _LOGGER.debug("Update cycle started")
        try:
            if self._needs_auth():
                await self._async_refresh_auth()

            if not self.cookie_header:
                raise ConfigEntryAuthFailed(
                    "No session cookie and no credentials are configured to "
                    "obtain one. Please reconfigure the integration."
                )

            raw = await self._async_fetch_usage()
        except MozillionAuthError as err:
            raw = await self._async_retry_after_auth_error(err)
        except (RuntimeError, ClientError) as err:
            _LOGGER.error("Update failed: %s", err)
            raise UpdateFailed(err) from err

        data = _build_coordinator_data(raw)
        data[ATTR_SIM_NUMBER] = self.config_entry.data.get(CONF_SIM_NUMBER, "")
        data[ATTR_ICCID] = self.config_entry.data.get(CONF_ICCID, "")

        _LOGGER.debug(
            "Update success: usage=%s, total=%s, remaining=%s, "
            "percentage=%s, unlimited=%s",
            data[ATTR_USAGE],
            data[ATTR_TOTAL],
            data[ATTR_REMAINING],
            data[ATTR_USAGE_PERCENTAGE],
            data[ATTR_UNLIMITED],
        )
        return data

    async def _async_retry_after_auth_error(
        self, error: MozillionAuthError
    ) -> dict[str, Any]:
        """Re-authenticate once after a rejected session, then retry the fetch.

        Only entries with credentials can recover; a cookie-only entry has to
        hand the problem to the user via a reauth flow.
        """

        if not (self.email and self.password):
            raise ConfigEntryAuthFailed(
                "Session expired and no credentials are configured to "
                "re-authenticate. Please update the integration's credentials or "
                "provide a fresh cookie."
            ) from error

        _LOGGER.warning("Mozillion session expired; re-authenticating and retrying")
        try:
            await self._async_refresh_auth()
            if not self.cookie_header:
                raise UpdateFailed(
                    "Re-authentication failed to obtain a session"
                ) from None
            return await self._async_fetch_usage()
        except MozillionAuthError as retry_error:
            # The credentials themselves are wrong: a retry will not help, so
            # ask the user instead of looping.
            raise ConfigEntryAuthFailed(
                "Mozillion rejected the configured credentials"
            ) from retry_error
        except (RuntimeError, ClientError) as retry_error:
            raise UpdateFailed(retry_error) from retry_error


def _build_coordinator_data(raw: dict[str, Any]) -> CoordinatorData:
    """Turn a completed usage payload into coordinator data.

    ``usedData``/``totalData`` are what the dashboard itself renders, so they
    drive the primary sensors. The GBR and global buckets are carried through
    as diagnostics -- they are unverified (see the integration docs).
    """

    usage = _to_float(raw.get("usedData"))
    total = _to_float(raw.get("totalData"))
    unlimited = bool(raw.get("isUnlimited"))

    remaining: float | None = None
    usage_percentage: float | None = None

    if usage is not None and total is not None and total > 0:
        # Mirror the dashboard: never show a negative balance or >100% used.
        remaining = max(0.0, total - usage)
        usage_percentage = min(100.0, (usage / total) * 100)

    return {
        ATTR_RAW: raw,
        ATTR_USAGE: usage,
        ATTR_TOTAL: total,
        ATTR_REMAINING: remaining,
        ATTR_USAGE_PERCENTAGE: usage_percentage,
        ATTR_UNLIMITED: unlimited,
        ATTR_USAGE_GBR: _to_float(raw.get("usedDataGbr")),
        ATTR_TOTAL_GBR: _to_float(raw.get("totalDataGbr")),
        ATTR_USAGE_GLOBAL: _to_float(raw.get("usedDataGlobal")),
        ATTR_TOTAL_GLOBAL: _to_float(raw.get("totalDataGlobal")),
    }
