"""Setup for the Mozillion integration.

The config entry is the *account*: it holds the credentials and the session. Each SIM
the account tracks is a subentry of type ``sim``, so adding a second SIM needs no
second copy of your password, and changing it is done once. A separate coordinator
polls each SIM, sharing one session through :class:`MozillionSession`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigSubentry,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_registry as er
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
    DOMAIN,
    SUBENTRY_TYPE_SIM,
)
from .coordinator import CoordinatorData, MozillionCoordinator
from .session import MozillionSession

_LOGGER = logging.getLogger(__name__)

CONFIG_ENTRY_VERSION = 3

PLATFORMS = [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN]

# Keys that existed before the dashboard moved to the sim_meta_id contract.
_LEGACY_KEYS = ("sim_plan_id", "usage_key", "remaining_key")
# Keys that describe one SIM. They used to sit on the entry, and belong to a
# subentry now that one entry can cover several SIMs.
_SIM_KEYS = (CONF_ORDER_DETAIL_ID, CONF_SIM_META_ID, CONF_SIM_NUMBER, CONF_ICCID)

__all__ = [
    "CONFIG_ENTRY_VERSION",
    "CoordinatorData",
    "MozillionAuthError",
    "MozillionConfigEntry",
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
    session: MozillionSession
    coordinators: dict[str, MozillionCoordinator] = field(default_factory=dict)


type MozillionConfigEntry = ConfigEntry[MozillionRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: MozillionConfigEntry) -> bool:
    """Set up the account and one coordinator per SIM."""

    session = MozillionSession.from_entry(entry)
    client = MozillionClient(async_get_clientsession(hass))
    update_interval = timedelta(
        seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )

    subentries = [
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_SIM
    ]
    if not subentries:
        raise ConfigEntryError(
            "This Mozillion account has no SIMs configured. Add one from the "
            "integration page."
        )

    coordinators: dict[str, MozillionCoordinator] = {}
    for subentry in subentries:
        coordinator = MozillionCoordinator(
            hass=hass,
            client=client,
            entry=entry,
            subentry=subentry,
            session=session,
            update_interval=update_interval,
        )
        # Raises ConfigEntryAuthFailed / ConfigEntryNotReady on its own. The first
        # SIM to poll logs in; the rest reuse the same session rather than logging
        # in again.
        await coordinator.async_config_entry_first_refresh()
        coordinators[subentry.subentry_id] = coordinator

    entry.runtime_data = MozillionRuntimeData(
        client=client, session=session, coordinators=coordinators
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Adding, removing or reconfiguring a SIM changes the entity set, so reload.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the account's subentries change."""

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older entries onto the account-hub shape.

    Version 2 introduced the ``sim_meta_id`` contract; version 3 moved a SIM's ids
    off the entry and into a subentry, so one entry can hold an account's worth of
    SIMs and the credentials live in one place.
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

    if entry.version == 1 and not await _async_migrate_to_v2(hass, entry):
        return False

    return await _async_migrate_to_v3(hass, entry)


async def _async_migrate_to_v3(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move the entry's SIM into a subentry."""

    data = dict(entry.data)
    sim_data = {key: data.pop(key, "") for key in _SIM_KEYS}
    for key in _LEGACY_KEYS:
        data.pop(key, None)

    sim_meta_id = str(sim_data.get(CONF_SIM_META_ID) or "").strip()
    if not sim_meta_id:
        _LOGGER.error(
            "Cannot migrate Mozillion entry %s: it holds no SIM to move into a "
            "subentry. Reconfigure the integration.",
            entry.entry_id,
        )
        await _async_prompt_reconfigure(hass, entry)
        return False

    sim_number = str(sim_data.get(CONF_SIM_NUMBER) or "").strip()
    subentry = ConfigSubentry(
        data=MappingProxyType(dict(sim_data)),
        subentry_type=SUBENTRY_TYPE_SIM,
        # The entry title used to be the SIM's name; that name belongs to the SIM.
        title=sim_number or entry.title,
        unique_id=sim_meta_id,
    )

    # Subentries have their own API -- async_update_entry does not take them.
    # Adding it first also means a duplicate SIM unique_id aborts the migration
    # before anything is written.
    if not hass.config_entries.async_add_subentry(entry, subentry):
        _LOGGER.error(
            "Cannot migrate Mozillion entry %s: the SIM subentry was refused",
            entry.entry_id,
        )
        return False
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        version=CONFIG_ENTRY_VERSION,
    )

    _async_rekey_entities(hass, entry, subentry.subentry_id)
    _LOGGER.info(
        "Migrated Mozillion entry %s to an account with SIM %s",
        entry.entry_id,
        sim_meta_id,
    )
    return True


def _async_rekey_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry_id: str,
) -> None:
    """Re-point the entry's existing entities at its new subentry.

    Every entity this integration creates is keyed ``<entry_id>_<suffix>``, so the
    subentry id is inserted after the entry id rather than matched against a list of
    known suffixes, which would drift the first time an entity was added.

    Rewriting ``unique_id`` rather than letting the entities be recreated keeps their
    ``entity_id``, and so their history and statistics, across the restructure.
    """

    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = entity.unique_id or ""
        if not unique_id.startswith(prefix):
            continue
        try:
            registry.async_update_entity(
                entity.entity_id,
                new_unique_id=f"{prefix}{subentry_id}_{unique_id[len(prefix) :]}",
                config_subentry_id=subentry_id,
            )
        except ValueError as err:
            # A collision would already be a broken registry; say so and carry on
            # rather than failing the whole migration.
            _LOGGER.warning(
                "Could not re-key Mozillion entity %s during migration: %s",
                entity.entity_id,
                err,
            )


async def _async_migrate_to_v2(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Re-discover the SIM this entry was created for and rewrite its data."""

    data = dict(entry.data)
    client = MozillionClient(async_get_clientsession(hass))

    cookie_header = data.get(CONF_SESSION_COOKIE) or ""
    xsrf_token = data.get(CONF_XSRF_TOKEN)
    # Coerced to str so the login closure below is typed rather than Any.
    email = str(data.get(CONF_EMAIL) or "")
    password = str(data.get(CONF_PASSWORD) or "")
    can_log_in = bool(email and password)

    async def log_in() -> None:
        """Replace the stored session with a freshly authenticated one."""
        nonlocal cookie_header, xsrf_token
        cookie_header, xsrf_token = await client.async_login(
            email=email,
            password=password,
            totp_secret=data.get(CONF_TOTP_SECRET) or None,
            origin=data.get(CONF_ORIGIN, DEFAULT_ORIGIN),
        )

    async def read_sims() -> list[MozillionSim]:
        """Read the SIM list, recovering from a stale stored session.

        The stored cookie is very often long expired -- the integration was
        broken upstream for months, so the session it holds has aged out. A
        rejected cookie must fall back to the credentials rather than failing
        the whole migration.
        """
        if not cookie_header:
            if not can_log_in:
                raise MozillionAuthError("no stored session and no credentials")
            await log_in()

        try:
            return await client.async_fetch_sims(
                cookie_header=cookie_header, xsrf_token=xsrf_token
            )
        except MozillionAuthError:
            if not can_log_in:
                raise
            _LOGGER.info(
                "Stored Mozillion session for entry %s has expired; logging in "
                "again before migrating",
                entry.entry_id,
            )
            await log_in()
            return await client.async_fetch_sims(
                cookie_header=cookie_header, xsrf_token=xsrf_token
            )

    try:
        sims = await read_sims()
    except MozillionAuthError as err:
        _LOGGER.error(
            "Cannot migrate Mozillion entry %s: the stored session has expired "
            "and there are no credentials to renew it (%s). Reconfigure the "
            "integration with your email and password, or a fresh cookie.",
            entry.entry_id,
            err,
        )
        await _async_prompt_reconfigure(hass, entry)
        return False
    except RuntimeError as err:
        _LOGGER.error("Cannot migrate Mozillion entry %s: %s", entry.entry_id, err)
        return False

    sim = _select_sim_for_migration(sims, data)
    if sim is None:
        _LOGGER.error(
            "Cannot migrate Mozillion entry %s: none of the %d SIM(s) on the "
            "dashboard match the stored SIM number %r. Reconfigure the "
            "integration to pick the right SIM.",
            entry.entry_id,
            len(sims),
            data.get(CONF_SIM_NUMBER, ""),
        )
        await _async_prompt_reconfigure(hass, entry)
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
        version=2,
    )
    _LOGGER.info(
        "Migrated Mozillion entry %s to SIM %s", entry.entry_id, sim.sim_meta_id
    )
    return True


async def _async_prompt_reconfigure(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask the user to re-enter their details, since migration cannot proceed.

    Migration returning ``False`` on failure only leaves a log line the user never
    sees. Starting the reconfigure flow gives them a UI path back instead of a
    silently dead entry.
    """

    try:
        existing = hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, include_uninitialized=True
        )
        if any(flow["context"].get("entry_id") == entry.entry_id for flow in existing):
            return
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
    except Exception:
        _LOGGER.exception(
            "Could not start the Mozillion reconfigure flow for entry %s",
            entry.entry_id,
        )


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
