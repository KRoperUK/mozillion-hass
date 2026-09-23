"""Tests for the config entry migration onto the sim_meta_id contract."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.mozillion import CONFIG_ENTRY_VERSION, async_migrate_entry
from custom_components.mozillion.api import MozillionAuthError, MozillionSim
from custom_components.mozillion.const import (
    ATTR_USAGE,
    CONF_ICCID,
    CONF_ORDER_DETAIL_ID,
    CONF_SESSION_COOKIE,
    CONF_SIM_META_ID,
    CONF_SIM_NUMBER,
    CONF_XSRF_TOKEN,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import MOCK_ENTRY_DATA_LOGIN, MOCK_SIM, sim_subentry

pytestmark = pytest.mark.asyncio

CLIENT = "custom_components.mozillion.MozillionClient"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


def _legacy_entry(
    hass: HomeAssistant, version: int = 1, **overrides
) -> MockConfigEntry:
    """An entry as written by the pre-sim_meta_id config flow."""

    data = {
        **MOCK_ENTRY_DATA_LOGIN,
        CONF_SESSION_COOKIE: "mozillion_session=old",
        CONF_XSRF_TOKEN: "old-xsrf",
        # A v1 entry held these on the entry; v3 moves them into a SIM subentry.
        CONF_ORDER_DETAIL_ID: "1234567",
        CONF_SIM_NUMBER: "07700900000",
        "sim_plan_id": "1234",
        "usage_key": "usedData",
        "remaining_key": "totalData",
    }
    data.pop(CONF_SIM_META_ID, None)
    data.update(overrides)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mozillion 07700900000",
        data=data,
        unique_id="1234567",
        version=version,
    )
    entry.add_to_hass(hass)
    return entry


class TestMigrateToV2:
    """The v1 -> v2 rewrite."""

    async def test_migrates_matching_sim(self, hass: HomeAssistant) -> None:
        entry = _legacy_entry(hass)
        client = MagicMock()
        client.async_fetch_sims = AsyncMock(return_value=[MOCK_SIM])

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is True

        assert entry.version == CONFIG_ENTRY_VERSION
        assert sim_subentry(entry).data[CONF_SIM_META_ID] == "7654321"
        assert sim_subentry(entry).data[CONF_ORDER_DETAIL_ID] == "1234567"
        assert sim_subentry(entry).data[CONF_ICCID] == "89440000000000000000"
        assert entry.unique_id == "7654321"
        # Legacy keys are gone from both data and options.
        for key in ("sim_plan_id", "usage_key", "remaining_key"):
            assert key not in entry.data

    async def test_uses_the_stored_cookie_without_logging_in(
        self, hass: HomeAssistant
    ) -> None:
        entry = _legacy_entry(hass)
        client = MagicMock()
        client.async_fetch_sims = AsyncMock(return_value=[MOCK_SIM])
        client.async_login = AsyncMock()

        with patch(CLIENT, return_value=client):
            await async_migrate_entry(hass, entry)

        client.async_login.assert_not_called()
        assert entry.data[CONF_SESSION_COOKIE] == "mozillion_session=old"

    async def test_logs_in_when_no_cookie_is_stored(self, hass: HomeAssistant) -> None:
        entry = _legacy_entry(hass, **{CONF_SESSION_COOKIE: "", CONF_XSRF_TOKEN: ""})
        client = MagicMock()
        client.async_login = AsyncMock(return_value=("fresh=1", "new-xsrf"))
        client.async_fetch_sims = AsyncMock(return_value=[MOCK_SIM])

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is True

        client.async_login.assert_called_once()
        assert entry.data[CONF_SESSION_COOKIE] == "fresh=1"

    async def test_picks_the_sim_matching_the_stored_number(
        self, hass: HomeAssistant
    ) -> None:
        other = MozillionSim(
            sim_meta_id="55555",
            order_detail_id="11111",
            sim_number="07700000001",
        )
        entry = _legacy_entry(hass)
        client = MagicMock()
        client.async_fetch_sims = AsyncMock(return_value=[other, MOCK_SIM])

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is True

        assert sim_subentry(entry).data[CONF_SIM_META_ID] == "7654321"

    async def test_ambiguous_multi_sim_account_fails(self, hass: HomeAssistant) -> None:
        """Ambiguity must not silently attach the wrong SIM.

        Both SIMs belong to the stored order and neither number matches, so
        there is nothing to disambiguate on.
        """
        other = MozillionSim(
            sim_meta_id="55555",
            order_detail_id="1234567",
            sim_number="07700000001",
        )
        entry = _legacy_entry(hass, **{CONF_SIM_NUMBER: "07000000000"})
        client = MagicMock()
        client.async_fetch_sims = AsyncMock(return_value=[other, MOCK_SIM])

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is False

        assert entry.version == 1
        assert CONF_SIM_META_ID not in entry.data

    async def test_dashboard_failure_leaves_the_entry_alone(
        self, hass: HomeAssistant
    ) -> None:
        entry = _legacy_entry(hass)
        client = MagicMock()
        client.async_fetch_sims = AsyncMock(side_effect=RuntimeError("site down"))

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is False

        assert entry.version == 1

    async def test_no_credentials_fails(self, hass: HomeAssistant) -> None:
        entry = _legacy_entry(
            hass,
            **{
                CONF_SESSION_COOKIE: "",
                CONF_XSRF_TOKEN: "",
                "email": "",
                "password": "",
            },
        )

        with patch(CLIENT) as client_cls:
            assert await async_migrate_entry(hass, entry) is False

        client_cls.return_value.async_fetch_sims.assert_not_called()

    async def test_current_version_is_a_no_op(self, hass: HomeAssistant) -> None:
        entry = _legacy_entry(hass, version=CONFIG_ENTRY_VERSION)

        with patch(CLIENT) as client_cls:
            assert await async_migrate_entry(hass, entry) is True

        client_cls.assert_not_called()

    async def test_newer_version_is_refused(self, hass: HomeAssistant) -> None:
        """A future entry must not be downgraded by an older integration."""
        entry = _legacy_entry(hass, version=CONFIG_ENTRY_VERSION + 1)

        with patch(CLIENT) as client_cls:
            assert await async_migrate_entry(hass, entry) is False

        client_cls.assert_not_called()


class TestStaleStoredSession:
    """The stored cookie is usually long expired by the time we migrate.

    The integration was broken upstream for months, so a legacy entry's session
    has almost certainly aged out. Migration must renew it from the stored
    credentials instead of giving up -- this is the reported failure.
    """

    async def test_expired_cookie_falls_back_to_the_credentials(
        self, hass: HomeAssistant
    ) -> None:
        entry = _legacy_entry(hass, **{CONF_SESSION_COOKIE: "expired=1"})

        client = MagicMock()
        client.async_login = AsyncMock(return_value=("fresh=1", "new-xsrf"))
        client.async_fetch_sims = AsyncMock(
            side_effect=[MozillionAuthError("login page"), [MOCK_SIM]]
        )

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is True

        client.async_login.assert_awaited_once()
        assert entry.version == CONFIG_ENTRY_VERSION
        assert sim_subentry(entry).data[CONF_SIM_META_ID] == "7654321"
        # The refreshed session must be stored, not the expired one.
        assert entry.data[CONF_SESSION_COOKIE] == "fresh=1"
        assert entry.data[CONF_XSRF_TOKEN] == "new-xsrf"

    async def test_expired_cookie_without_credentials_prompts_reconfigure(
        self, hass: HomeAssistant
    ) -> None:
        """A cookie-only entry has no way to renew itself, so ask the user."""
        entry = _legacy_entry(
            hass,
            **{
                CONF_SESSION_COOKIE: "expired=1",
                "email": "",
                "password": "",
            },
        )

        client = MagicMock()
        client.async_login = AsyncMock()
        client.async_fetch_sims = AsyncMock(
            side_effect=MozillionAuthError("login page")
        )

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is False

        client.async_login.assert_not_called()
        assert entry.version == 1

        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert any(
            flow["context"]["source"] == "reconfigure"
            and flow["context"].get("entry_id") == entry.entry_id
            for flow in flows
        ), "the user was left with no way forward"

    async def test_ambiguous_match_prompts_reconfigure(
        self, hass: HomeAssistant
    ) -> None:
        other = MozillionSim(
            sim_meta_id="55555",
            order_detail_id="1234567",
            sim_number="07700000001",
        )
        entry = _legacy_entry(hass, **{CONF_SIM_NUMBER: "07000000000"})

        client = MagicMock()
        client.async_fetch_sims = AsyncMock(return_value=[other, MOCK_SIM])

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is False

        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert any(flow["context"]["source"] == "reconfigure" for flow in flows)

    async def test_no_duplicate_prompt_when_one_is_already_open(
        self, hass: HomeAssistant
    ) -> None:
        """Retried setups must not stack reconfigure flows."""
        entry = _legacy_entry(
            hass,
            **{CONF_SESSION_COOKIE: "expired=1", "email": "", "password": ""},
        )
        client = MagicMock()
        client.async_fetch_sims = AsyncMock(
            side_effect=MozillionAuthError("login page")
        )

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is False
            assert await async_migrate_entry(hass, entry) is False

        flows = [
            flow
            for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            if flow["context"].get("entry_id") == entry.entry_id
        ]
        assert len(flows) == 1


class TestEntityRekey:
    """Migration re-points the entry's existing entities at the new subentry.

    Rewriting unique_ids rather than letting entities be recreated is what keeps
    their entity_id -- and so their history and statistics -- through the
    restructure, so it is worth pinning down.
    """

    async def test_existing_entities_are_rekeyed_to_the_subentry(
        self, hass: HomeAssistant
    ) -> None:
        entry = _legacy_entry(hass)
        registry = er.async_get(hass)
        existing = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}_{ATTR_USAGE}",
            config_entry=entry,
            suggested_object_id="mozillion_usage",
        )
        entity_id = existing.entity_id

        client = MagicMock()
        client.async_fetch_sims = AsyncMock(return_value=[MOCK_SIM])

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is True

        subentry = sim_subentry(entry)
        migrated = registry.async_get(entity_id)
        assert migrated is not None, "the entity lost its identity in the migration"
        assert migrated.unique_id == (
            f"{entry.entry_id}_{subentry.subentry_id}_{ATTR_USAGE}"
        )
        assert migrated.config_subentry_id == subentry.subentry_id

    async def test_unrelated_entities_are_left_alone(self, hass: HomeAssistant) -> None:
        """Only this integration's entities are touched."""
        entry = _legacy_entry(hass)
        registry = er.async_get(hass)
        other = registry.async_get_or_create(
            "sensor",
            "someone_else",
            "not-ours",
            config_entry=entry,
            suggested_object_id="not_ours",
        )

        client = MagicMock()
        client.async_fetch_sims = AsyncMock(return_value=[MOCK_SIM])

        with patch(CLIENT, return_value=client):
            assert await async_migrate_entry(hass, entry) is True

        assert registry.async_get(other.entity_id).unique_id == "not-ours"
