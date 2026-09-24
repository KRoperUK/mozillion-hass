"""Diagnostics support for the Mozillion integration.

The entry is the account and each SIM is a subentry, so this reports both: what the
account holds, what each SIM holds, and how each SIM's poll is doing.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_EMAIL,
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
)

# Everything here identifies the account or lets someone else use the session.
#
# "title" is here because the titles carry identifiers: the account title is the
# login email, and a SIM's title is its display name, which contains the phone
# number. Both appear in a diagnostics download that users attach to bug reports.
TO_REDACT = {
    "title",
    CONF_EMAIL,
    CONF_ICCID,
    # Mozillion's own ids for the order and the SIM: not credentials, but they
    # identify the account just as the ICCID does.
    CONF_ORDER_DETAIL_ID,
    CONF_SIM_META_ID,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    runtime = entry.runtime_data

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "version": entry.version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "subentries": {
                subentry_id: {
                    "type": subentry.subentry_type,
                    "title": subentry.title,
                    "data": dict(subentry.data),
                }
                for subentry_id, subentry in entry.subentries.items()
            },
            "coordinators": {
                subentry_id: {
                    "last_update_success": coordinator.last_update_success,
                    "last_exception": (
                        str(coordinator.last_exception)
                        if coordinator.last_exception
                        else None
                    ),
                    "update_interval": str(coordinator.update_interval),
                    "data": coordinator.data,
                }
                for subentry_id, coordinator in runtime.coordinators.items()
            },
        },
        TO_REDACT,
    )
