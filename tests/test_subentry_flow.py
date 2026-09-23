"""Tests for adding a SIM to an existing account through the subentry flow.

The subentry flow is the hub's headline feature: one account entry holding the
credentials, with a subentry per SIM. Nothing else in the suite drove it, which
left the whole path uncovered -- including Home Assistant's own lookup of the
handler's supported subentry types, which is where the hook was declared as an
instance method and raised.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.mozillion.const import (
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    SUBENTRY_TYPE_SIM,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import (
    MOCK_API_RESPONSE,
    MOCK_SIM,
    MOCK_WALLET,
    _make_config_entry,
    sim_subentry,
)

pytestmark = pytest.mark.asyncio

CLIENT = "custom_components.mozillion.MozillionClient"
FLOW_CLIENT = "custom_components.mozillion.config_flow.MozillionClient"

SECOND_SIM = dataclasses.replace(
    MOCK_SIM,
    sim_meta_id="9999999",
    order_detail_id="8888888",
    sim_number="07700900001",
    iccid="89440000000000000001",
    label="Second SIM",
)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


def _client(sims: list[Any]) -> MagicMock:
    client = MagicMock()
    client.async_get_usage = AsyncMock(return_value=MOCK_API_RESPONSE)
    client.async_login = AsyncMock(
        return_value=("mozillion_session=abc; XSRF-TOKEN=xyz", "xyz")
    )
    client.async_fetch_sims = AsyncMock(return_value=sims)
    client.async_fetch_sim = AsyncMock(
        side_effect=lambda sim_meta_id, **kwargs: next(
            (sim for sim in sims if sim.sim_meta_id == sim_meta_id), MOCK_SIM
        )
    )
    client.async_fetch_overspend = AsyncMock(return_value=MOCK_WALLET)
    return client


@asynccontextmanager
async def _account(
    hass: HomeAssistant, client: MagicMock
) -> AsyncIterator[MockConfigEntry]:
    """Set the account entry up with the API mocked, and hold the mock open.

    The client is patched for the whole block rather than only for setup: the
    subentry flow builds its own client, and it looks the class up in
    ``config_flow``, which is a separate reference from the one entry setup uses.
    Patching only one of them let the flow reach the real network.
    """

    entry = _make_config_entry()
    entry.add_to_hass(hass)
    with (
        patch(CLIENT, return_value=client),
        patch(FLOW_CLIENT, return_value=client),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        yield entry


async def _start_flow(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    """Start the flow the way the UI does."""

    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SIM),
        context={"source": config_entries.SOURCE_USER},
    )


def _sim_subentries(entry: MockConfigEntry) -> list[ConfigSubentry]:
    return [
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_SIM
    ]


def _offered_sims(result: dict[str, Any]) -> list[str]:
    """Return the SIM labels the form offers."""

    schema = result["data_schema"].schema
    marker = next(iter(schema))
    return list(schema[marker].container)


async def test_the_flow_starts_at_all(hass: HomeAssistant) -> None:
    """HA has to be able to enumerate the supported subentry types.

    ConfigSubentryFlowManager.async_create_flow looks the handler up and calls
    ``async_get_supported_subentry_types`` on the class, so a broken hook fails
    here, before any step runs.
    """

    async with _account(hass, _client([MOCK_SIM, SECOND_SIM])) as entry:
        result = await _start_flow(hass, entry)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_only_untracked_sims_are_offered(hass: HomeAssistant) -> None:
    """The SIM already on the account must not be offered again."""

    async with _account(hass, _client([MOCK_SIM, SECOND_SIM])) as entry:
        result = await _start_flow(hass, entry)

    assert _offered_sims(result) == [SECOND_SIM.display_name]


async def test_adding_a_sim_creates_a_subentry(hass: HomeAssistant) -> None:
    """The account keeps its credentials; the SIM becomes a second subentry."""

    async with _account(hass, _client([MOCK_SIM, SECOND_SIM])) as entry:
        original_data = dict(entry.data)

        result = await _start_flow(hass, entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"sim": SECOND_SIM.display_name}
        )
        await hass.async_block_till_done()

        subentries = _sim_subentries(entry)
        added = next(
            subentry
            for subentry in subentries
            if subentry.unique_id == SECOND_SIM.sim_meta_id
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(subentries) == 2
    assert added.data[CONF_ORDER_DETAIL_ID] == SECOND_SIM.order_detail_id
    assert added.data[CONF_SIM_NUMBER] == SECOND_SIM.sim_number
    assert added.data[CONF_ICCID] == SECOND_SIM.iccid
    assert added.title == SECOND_SIM.display_name

    # The credentials live on the account entry and are untouched by adding a SIM.
    assert dict(entry.data) == original_data
    assert sim_subentry(entry).subentry_id != added.subentry_id


async def test_a_fully_tracked_account_aborts(hass: HomeAssistant) -> None:
    """With every SIM already on the account there is nothing to add."""

    async with _account(hass, _client([MOCK_SIM])) as entry:
        result = await _start_flow(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_new_sims"


async def test_an_unreadable_dashboard_falls_back_to_manual_ids(
    hass: HomeAssistant,
) -> None:
    """When the SIM list cannot be read, the ids can still be entered by hand."""

    client = _client([MOCK_SIM])
    client.async_fetch_sims = AsyncMock(side_effect=RuntimeError("dashboard is down"))

    async with _account(hass, client) as entry:
        result = await _start_flow(hass, entry)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_ids"


async def test_manual_ids_create_the_subentry(hass: HomeAssistant) -> None:
    """Entering ids by hand still validates against the API before saving."""

    client = _client([MOCK_SIM])
    # The dashboard read fails once and then recovers. `_async_client_with_session`
    # probes the session with the same call, so a permanent failure would make the
    # validation in `_async_finish` fail too and the SIM could never be saved.
    calls = {"n": 0}

    def _flaky(**kwargs: Any) -> list[Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dashboard is down")
        return [MOCK_SIM, SECOND_SIM]

    client.async_fetch_sims = AsyncMock(side_effect=_flaky)

    async with _account(hass, client) as entry:
        result = await _start_flow(hass, entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_ORDER_DETAIL_ID: SECOND_SIM.order_detail_id,
                CONF_SIM_META_ID: SECOND_SIM.sim_meta_id,
                CONF_SIM_NUMBER: SECOND_SIM.sim_number,
            },
        )
        await hass.async_block_till_done()

        added = next(
            subentry
            for subentry in _sim_subentries(entry)
            if subentry.unique_id == SECOND_SIM.sim_meta_id
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert added.data[CONF_SIM_META_ID] == SECOND_SIM.sim_meta_id


async def test_manual_ids_report_a_sim_the_api_will_not_confirm(
    hass: HomeAssistant,
) -> None:
    """A SIM the API rejects must not be saved."""

    client = _client([MOCK_SIM])
    client.async_fetch_sims = AsyncMock(side_effect=RuntimeError("dashboard is down"))
    client.async_fetch_sim = AsyncMock(side_effect=RuntimeError("no such SIM"))

    async with _account(hass, client) as entry:
        result = await _start_flow(hass, entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_ORDER_DETAIL_ID: SECOND_SIM.order_detail_id,
                CONF_SIM_META_ID: SECOND_SIM.sim_meta_id,
                CONF_SIM_NUMBER: SECOND_SIM.sim_number,
            },
        )

        assert len(_sim_subentries(entry)) == 1

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_the_flow_refuses_an_entry_that_is_not_loaded(
    hass: HomeAssistant,
) -> None:
    """A subentry cannot be added to an entry that is not running."""

    entry = _make_config_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SIM),
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"


async def _start_reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, subentry: ConfigSubentry
) -> dict[str, Any]:
    """Start a reconfigure flow for one subentry, the way the UI does."""

    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SIM),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )


async def test_reconfigure_offers_every_sim_including_the_current_one(
    hass: HomeAssistant,
) -> None:
    """The reconfigure form lists every SIM, the current one included.

    The subentry being reconfigured is left out of the "already tracked" set, so
    the SIM it already points at stays selectable -- picking it re-validates and
    rewrites the same data rather than moving the subentry.
    """

    async with _account(hass, _client([MOCK_SIM, SECOND_SIM])) as entry:
        result = await _start_reconfigure(hass, entry, sim_subentry(entry))

    assert result["type"] is FlowResultType.FORM
    # The step id is load-bearing, not cosmetic: HA routes the submitted form by
    # it, so reporting "user" here sent the submission to the wrong step.
    assert result["step_id"] == "reconfigure"
    assert _offered_sims(result) == [
        MOCK_SIM.display_name,
        SECOND_SIM.display_name,
    ]


async def test_reconfigure_moves_the_subentry_rather_than_adding_one(
    hass: HomeAssistant,
) -> None:
    """Reconfigure updates in place, so entity history is not orphaned."""

    async with _account(hass, _client([MOCK_SIM, SECOND_SIM])) as entry:
        before = sim_subentry(entry)
        subentry_id = before.subentry_id

        result = await _start_reconfigure(hass, entry, before)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"sim": SECOND_SIM.display_name}
        )
        await hass.async_block_till_done()

        after = entry.subentries[subentry_id]

    assert result["type"] is FlowResultType.ABORT
    # Same subentry, new SIM: the account still has exactly one SIM subentry.
    assert len(_sim_subentries(entry)) == 1
    assert after.data[CONF_ORDER_DETAIL_ID] == SECOND_SIM.order_detail_id
    assert after.data[CONF_SIM_NUMBER] == SECOND_SIM.sim_number
    assert after.data[CONF_ICCID] == SECOND_SIM.iccid
    assert after.title == SECOND_SIM.display_name


def _entity_ids(hass: HomeAssistant) -> set[str]:
    return {state.entity_id for state in hass.states.async_all()}


async def test_adding_a_sim_produces_its_entities(hass: HomeAssistant) -> None:
    """The whole point of the subentry flow: a new SIM gets its own entities.

    The flow only creates the subentry. Entities appear because the entry reloads
    when its subentries change, so this is the end-to-end path a user sees, and
    the one thing the rest of this file does not cover.
    """

    async with _account(hass, _client([MOCK_SIM, SECOND_SIM])) as entry:
        before = _entity_ids(hass)

        result = await _start_flow(hass, entry)
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"sim": SECOND_SIM.display_name}
        )
        await hass.async_block_till_done()

        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)

    added = _entity_ids(hass) - before

    assert added, "adding a SIM must create its entities, not just a subentry"
    # Entity ids carry the device name, which carries the SIM's number.
    assert any(SECOND_SIM.sim_number in entity_id for entity_id in added), sorted(added)
    # The first SIM keeps its own entities, and each SIM gets its own device.
    assert any(MOCK_SIM.sim_number in entity_id for entity_id in _entity_ids(hass))
    assert len(devices) == 2, [device.name for device in devices]


async def test_removing_a_sim_removes_its_entities(hass: HomeAssistant) -> None:
    """The other half of dynamic devices: removal cleans up after itself.

    Home Assistant clears the device and entity registry entries for a removed
    subentry, and the entry reloads, so the remaining SIM is unaffected.
    """

    async with _account(hass, _client([MOCK_SIM, SECOND_SIM])) as entry:
        result = await _start_flow(hass, entry)
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"sim": SECOND_SIM.display_name}
        )
        await hass.async_block_till_done()
        assert any(SECOND_SIM.sim_number in e for e in _entity_ids(hass))

        second = next(
            subentry
            for subentry in _sim_subentries(entry)
            if subentry.unique_id == SECOND_SIM.sim_meta_id
        )
        assert (
            hass.config_entries.async_remove_subentry(entry, second.subentry_id) is True
        )
        await hass.async_block_till_done()

        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)

    remaining = _entity_ids(hass)
    assert not any(SECOND_SIM.sim_number in entity_id for entity_id in remaining)
    assert any(MOCK_SIM.sim_number in entity_id for entity_id in remaining)
    assert len(devices) == 1, [device.name for device in devices]
