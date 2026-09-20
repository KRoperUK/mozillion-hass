"""Diagnostics support for the Mozillion integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_EMAIL,
    CONF_ICCID,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SIM_NUMBER,
    CONF_TOTP_SECRET,
    CONF_XSRF_TOKEN,
)

# Everything here identifies the account or lets someone else use the session.
TO_REDACT = {
    CONF_EMAIL,
    CONF_ICCID,
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

    coordinator = entry.runtime_data.coordinator

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "version": entry.version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "coordinator": {
                "last_update_success": coordinator.last_update_success,
                "last_exception": str(coordinator.last_exception)
                if coordinator.last_exception
                else None,
                "update_interval": str(coordinator.update_interval),
                "data": coordinator.data,
            },
        },
        TO_REDACT,
    )
