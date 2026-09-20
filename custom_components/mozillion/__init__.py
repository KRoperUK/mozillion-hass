"""Setup for the Mozillion integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MozillionAuthError, MozillionClient, MozillionSim
from .const import (
    CONF_EMAIL,
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_ORIGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
    DEFAULT_ORIGIN,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import CoordinatorData, MozillionCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_ENTRY_VERSION = 2

# Keys that existed before the dashboard moved to the sim_meta_id contract.
_LEGACY_KEYS = ("sim_plan_id", "usage_key", "remaining_key")

__all__ = [
    "CONFIG_ENTRY_VERSION",
    "CoordinatorData",
    "MozillionAuthError",
    "MozillionCoordinator",
    "MozillionRuntimeData",
    "async_migrate_entry",
    "async_setup_entry",
    "async_unload_entry",
]


@dataclass
class MozillionRuntimeData:
    """Objects shared with the entity platforms."""

    client: MozillionClient
    coordinator: MozillionCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mozillion from a config entry."""

    client = MozillionClient(async_get_clientsession(hass))

    # Authentication is left to the coordinator's first refresh: it already
    # knows how to log in, and doing it here as well logged in twice.
    coordinator = MozillionCoordinator(
        hass=hass,
        client=client,
        entry=entry,
        cookie_header=entry.data.get(CONF_SESSION_COOKIE),
        xsrf_header=entry.data.get(CONF_XSRF_TOKEN),
        update_interval=timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
    )

    _LOGGER.debug("Starting first refresh for entry %s", entry.entry_id)
    # Raises ConfigEntryAuthFailed / ConfigEntryNotReady on its own.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = MozillionRuntimeData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(
        entry, [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN]
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(
        entry, [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN]
    )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entries created before Mozillion introduced ``sim_meta_id``.

    The old contract identified a SIM by ``sim_plan_id``; the usage endpoints
    now take the dashboard's ``sim_meta_id`` instead. Nothing in the stored
    entry maps onto it, so the SIM list is re-read from the dashboard.
    """

    if entry.version > CONFIG_ENTRY_VERSION:
        _LOGGER.error(
            "Cannot downgrade Mozillion entry %s from version %s to %s",
            entry.entry_id,
            entry.version,
            CONFIG_ENTRY_VERSION,
        )
        return False

    if entry.version == CONFIG_ENTRY_VERSION:
        return True

    _LOGGER.info(
        "Migrating Mozillion entry %s from version %s to %s",
        entry.entry_id,
        entry.version,
        CONFIG_ENTRY_VERSION,
    )
    return await _async_migrate_to_v2(hass, entry)


async def _async_migrate_to_v2(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Re-discover the SIM this entry was created for and rewrite its data."""

    data = dict(entry.data)
    client = MozillionClient(async_get_clientsession(hass))

    cookie_header = data.get(CONF_SESSION_COOKIE) or ""
    xsrf_token = data.get(CONF_XSRF_TOKEN)
    email = data.get(CONF_EMAIL)
    password = data.get(CONF_PASSWORD)

    try:
        if not cookie_header and email and password:
            cookie_header, xsrf_token = await client.async_login(
                email=email,
                password=password,
                totp_secret=data.get(CONF_TOTP_SECRET) or None,
                origin=data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
            )
        if not cookie_header:
            _LOGGER.error(
                "Cannot migrate Mozillion entry %s: no usable credentials. "
                "Reconfigure the integration.",
                entry.entry_id,
            )
            return False

        sims = await client.async_fetch_sims(
            cookie_header=cookie_header, xsrf_token=xsrf_token
        )
    except RuntimeError as err:
        _LOGGER.error("Cannot migrate Mozillion entry %s: %s", entry.entry_id, err)
        return False

    sim = _select_sim_for_migration(sims, data)
    if sim is None:
        _LOGGER.error(
            "Cannot migrate Mozillion entry %s: none of the %d SIM(s) on the "
            "dashboard match the stored SIM number %r",
            entry.entry_id,
            len(sims),
            data.get(CONF_SIM_NUMBER, ""),
        )
        return False

    data[CONF_SIM_META_ID] = sim.sim_meta_id
    data[CONF_ORDER_DETAIL_ID] = sim.order_detail_id
    if sim.sim_number:
        data[CONF_SIM_NUMBER] = sim.sim_number
    if sim.iccid:
        data[CONF_ICCID] = sim.iccid
    data[CONF_SESSION_COOKIE] = cookie_header
    data[CONF_XSRF_TOKEN] = xsrf_token or ""
    for key in _LEGACY_KEYS:
        data.pop(key, None)

    options: dict[str, Any] = {
        key: value for key, value in entry.options.items() if key not in _LEGACY_KEYS
    }

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        unique_id=sim.sim_meta_id,
        version=CONFIG_ENTRY_VERSION,
    )
    _LOGGER.info(
        "Migrated Mozillion entry %s to SIM %s", entry.entry_id, sim.sim_meta_id
    )
    return True


def _select_sim_for_migration(
    sims: list[MozillionSim], data: dict[str, Any]
) -> MozillionSim | None:
    """Pick the SIM a legacy entry referred to.

    Legacy entries stored the SIM's phone number, so match on that first. When
    the account only has one SIM it is unambiguous anyway.
    """

    stored_number = str(data.get(CONF_SIM_NUMBER) or "").strip()
    if stored_number:
        for sim in sims:
            if sim.sim_number == stored_number:
                return sim

    matched = [
        sim for sim in sims if sim.order_detail_id == data.get(CONF_ORDER_DETAIL_ID)
    ]
    if len(matched) == 1:
        return matched[0]

    return sims[0] if len(sims) == 1 else None
