"""Setup for the Mozillion integration."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from aiohttp import ClientError
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MozillionAuthError, MozillionClient
from .const import (
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_SIM_NUMBER,
    ATTR_TOTAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    AUTH_REFRESH_THRESHOLD,
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
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CoordinatorData = dict[str, Any]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mozillion from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    client = MozillionClient(session)

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)
    totp_secret = entry.data.get(CONF_TOTP_SECRET) or None
    origin = entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN)

    usage_key = entry.options.get(
        CONF_USAGE_KEY, entry.data.get(CONF_USAGE_KEY, DEFAULT_USAGE_KEY)
    )
    remaining_key = entry.options.get(
        CONF_REMAINING_KEY, entry.data.get(CONF_REMAINING_KEY, DEFAULT_REMAINING_KEY)
    )

    cookie_header = entry.data.get(CONF_SESSION_COOKIE)
    xsrf_header = entry.data.get(CONF_XSRF_TOKEN)

    if email and password:
        _LOGGER.debug("Performing login during setup for entry %s", entry.entry_id)
        try:
            cookie_header, xsrf_header = await client.async_login(
                email=email,
                password=password,
                totp_secret=totp_secret,
                origin=origin,
            )
            _LOGGER.info("Login successful during setup")
        except RuntimeError as err:
            _LOGGER.error("Login failed during setup: %s", err)
            raise ConfigEntryNotReady(err) from err

    coordinator = MozillionCoordinator(
        hass=hass,
        client=client,
        entry=entry,
        usage_key=usage_key,
        remaining_key=remaining_key,
        cookie_header=cookie_header,
        xsrf_header=xsrf_header,
        update_interval=timedelta(seconds=scan_interval),
    )

    _LOGGER.debug("Starting first refresh for entry %s", entry.entry_id)
    try:
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.info("First refresh completed for entry %s", entry.entry_id)
    except UpdateFailed as err:
        _LOGGER.error("First refresh failed: %s", err)
        raise ConfigEntryNotReady from err

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(
        entry, [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN]
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN]
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


class MozillionCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator to poll the Mozillion endpoint."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MozillionClient,
        entry: ConfigEntry,
        usage_key: str,
        remaining_key: str,
        cookie_header: str | None,
        xsrf_header: str | None,
        update_interval: timedelta,
    ) -> None:
        self.client = client
        self.entry = entry
        self.usage_key = usage_key
        self.remaining_key = remaining_key
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

        if self.entry is None:
            return
        new_data = dict(self.entry.data)
        new_data[CONF_SESSION_COOKIE] = self.cookie_header or ""
        new_data[CONF_XSRF_TOKEN] = self.xsrf_header or ""
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch data from API, transparently re-authenticating on expiry."""

        _LOGGER.debug("Update cycle started")
        try:
            if self._needs_auth():
                await self._async_refresh_auth()

            if not self.cookie_header:
                _LOGGER.error("No cookies available for Mozillion request")
                raise RuntimeError("No cookies available for Mozillion request")

            raw = await self.client.async_get_usage(
                order_detail_id=self.entry.data[CONF_ORDER_DETAIL_ID],
                sim_plan_id=self.entry.data[CONF_SIM_PLAN_ID],
                cookie_header=self.cookie_header,
                xsrf_token=self.xsrf_header,
            )
        except MozillionAuthError:
            # The session expired mid-flight. Re-authenticate once and retry,
            # but only if we actually have credentials to do so.
            if self.email and self.password:
                _LOGGER.warning(
                    "Mozillion session expired; re-authenticating and retrying"
                )
                await self._async_refresh_auth()
                if not self.cookie_header:
                    raise UpdateFailed(
                        "Re-authentication failed to obtain a session"
                    ) from None
                try:
                    raw = await self.client.async_get_usage(
                        order_detail_id=self.entry.data[CONF_ORDER_DETAIL_ID],
                        sim_plan_id=self.entry.data[CONF_SIM_PLAN_ID],
                        cookie_header=self.cookie_header,
                        xsrf_token=self.xsrf_header,
                    )
                except MozillionAuthError as err:
                    raise UpdateFailed(
                        "Re-authentication did not restore access"
                    ) from err
            else:
                raise UpdateFailed(
                    "Session expired and no credentials are configured to "
                    "re-authenticate"
                ) from None
        except (RuntimeError, ClientError) as err:
            _LOGGER.error("Update failed: %s", err)
            raise UpdateFailed(err) from err

        usage = _deep_get(raw, self.usage_key)
        total = _deep_get(raw, self.remaining_key)
        unlimited = _deep_get(raw, "isUnlimited") or False

        # Calculate remaining as total - used
        remaining = None
        usage_percentage = None
        if total is not None and usage is not None:
            try:
                remaining = float(total) - float(usage)
                usage_percentage = (
                    (float(usage) / float(total)) * 100 if float(total) > 0 else 0
                )
            except (ValueError, TypeError):
                remaining = total

        _LOGGER.debug(
            "Update success: usage=%s, total=%s, remaining=%s, "
            "percentage=%s, unlimited=%s",
            usage,
            total,
            remaining,
            usage_percentage,
            unlimited,
        )

        return {
            ATTR_RAW: raw,
            ATTR_USAGE: usage,
            ATTR_TOTAL: total,
            ATTR_REMAINING: remaining,
            ATTR_USAGE_PERCENTAGE: usage_percentage,
            ATTR_UNLIMITED: unlimited,
            ATTR_SIM_NUMBER: self.entry.data.get(CONF_SIM_NUMBER, ""),
        }


def _deep_get(data: Any, dotted_key: str | None) -> Any:
    """Safely fetch nested value using dotted key."""

    if not dotted_key:
        return None

    current: Any = data
    for part in dotted_key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current
