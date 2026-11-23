"""Repair flows: fix a Cloudflare block without leaving the Repairs page."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from ._compat import schema
from .api import MozillionClient
from .const import CONF_FLARESOLVERR_URL, CONF_ORIGIN, DEFAULT_ORIGIN, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CloudflareBlockedRepairFlow(RepairsFlow):
    """Ask for a FlareSolverr address, prove it works, then reload the account."""

    def __init__(self, entry_id: str, issue_id: str) -> None:
        self._entry_id = entry_id
        self._issue_id = issue_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_solver()

    async def async_step_solver(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_missing")

        errors: dict[str, str] = {}
        url = str((user_input or {}).get(CONF_FLARESOLVERR_URL) or "").strip()
        if user_input is not None:
            # A real solve is the only proof the address works, and it is the
            # same call a login makes, so a bad address fails here rather than
            # on the next poll.
            client = MozillionClient(
                async_create_clientsession(self.hass), flaresolverr_url=url
            )
            try:
                await client.async_solve_challenge(
                    entry.data.get(CONF_ORIGIN, DEFAULT_ORIGIN)
                )
            except RuntimeError:
                _LOGGER.exception("FlareSolverr could not clear the challenge")
                errors["base"] = "solver_failed"
            else:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_FLARESOLVERR_URL: url}
                )
                # The update listener ignores data changes, and the client was
                # built without the solver, so reload to pick the address up.
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_create_entry(data={})

        stored = str(entry.data.get(CONF_FLARESOLVERR_URL) or "")
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self._issue_id)
        return self.async_show_form(
            step_id="solver",
            description_placeholders=issue.translation_placeholders if issue else None,
            data_schema=schema.Schema(
                {
                    schema.Required(
                        CONF_FLARESOLVERR_URL,
                        description={"suggested_value": url or stored},
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                    )
                }
            ),
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the flow for a fixable repair."""

    entry_id = str((data or {}).get("entry_id") or "")
    return CloudflareBlockedRepairFlow(entry_id, issue_id)
