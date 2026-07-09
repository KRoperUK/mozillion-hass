"""Setup for the Mozillion integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import MozillionAuthError, MozillionClient
from .const import (
    CONF_EMAIL,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_REMAINING_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_TOTP_SECRET,
    CONF_USAGE_KEY,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
    DEFAULT_REMAINING_KEY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USAGE_KEY,
    DOMAIN,
)
from .coordinator import CoordinatorData, MozillionCoordinator, _deep_get

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "CoordinatorData",
    "MozillionAuthError",
    "MozillionCoordinator",
    "_deep_get",
    "async_setup_entry",
    "async_unload_entry",
]


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
    except ConfigEntryAuthFailed as err:
        _LOGGER.error("Auth failed during first refresh: %s", err)
        raise
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
