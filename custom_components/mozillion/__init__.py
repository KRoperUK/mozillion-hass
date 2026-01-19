"""Setup for the Mozillion integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN

from .api import MozillionClient
from .const import (
    ATTR_RAW,
    ATTR_REMAINING,
    ATTR_SIM_NUMBER,
    ATTR_TOTAL,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
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
    DEFAULT_REMAINING_KEY,
    DEFAULT_ORIGIN,
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

    await hass.config_entries.async_forward_entry_setups(entry, [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN])
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

        super().__init__(
            hass,
            _LOGGER,
            name="Mozillion Data",
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch data from API."""

        _LOGGER.debug("Update cycle started")
        try:
            if not self.cookie_header and self.email and self.password:
                _LOGGER.debug("Re-logging in to refresh cookies")
                self.cookie_header, self.xsrf_header = await self.client.async_login(
                    email=self.email,
                    password=self.password,
                    totp_secret=self.totp_secret,
                    origin=self.origin,
                )

            if not self.cookie_header:
                _LOGGER.error("No cookies available for Mozillion request")
                raise RuntimeError("No cookies available for Mozillion request")

            raw = await self.client.async_get_usage(
                order_detail_id=self.entry.data[CONF_ORDER_DETAIL_ID],
                sim_plan_id=self.entry.data[CONF_SIM_PLAN_ID],
                cookie_header=self.cookie_header,
                xsrf_token=self.xsrf_header,
            )
        except Exception as err:  # noqa: BLE001
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
                usage_percentage = (float(usage) / float(total)) * 100 if float(total) > 0 else 0
            except (ValueError, TypeError):
                remaining = total
        
        _LOGGER.debug("Update success: usage=%s, total=%s, remaining=%s, percentage=%s, unlimited=%s", 
                     usage, total, remaining, usage_percentage, unlimited)

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
