"""Binary sensor platform for Mozillion data usage."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MozillionConfigEntry
from .const import (
    ATTR_OVERSPEND_LIMIT_REACHED,
    ATTR_UNLIMITED,
    ATTR_USAGE,
    ATTR_USAGE_PERCENTAGE,
    ATTR_WALLET,
    SUBENTRY_TYPE_SIM,
)
from .coordinator import MozillionCoordinator, wallet_is_active
from .entity import MozillionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MozillionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a binary sensor set for every SIM on the account."""

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SIM:
            continue

        coordinator = entry.runtime_data.coordinators[subentry.subentry_id]
        async_add_entities(
            [
                MozillionUnlimitedSensor(coordinator, entry, subentry),
                MozillionOverspendSensor(coordinator, entry, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class MozillionUnlimitedSensor(MozillionEntity, BinarySensorEntity):
    """Representation of Mozillion unlimited boolean sensor."""

    _attr_translation_key = "unlimited"

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialize the unlimited sensor."""
        super().__init__(coordinator, entry, subentry, ATTR_UNLIMITED)

    @property
    def is_on(self) -> bool:
        """Return True when the plan has no data cap."""
        return bool(self.coordinator.data.get(ATTR_UNLIMITED))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the figures the unlimited flag is derived from."""

        data = self.coordinator.data
        return {
            ATTR_USAGE: data.get(ATTR_USAGE),
            ATTR_USAGE_PERCENTAGE: data.get(ATTR_USAGE_PERCENTAGE),
        }


class MozillionOverspendSensor(MozillionEntity, BinarySensorEntity):
    """Report whether the out-of-bundle (wallet) limit has been reached."""

    _attr_translation_key = "overspend_limit_reached"

    def __init__(
        self,
        coordinator: MozillionCoordinator,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialize the overspend sensor."""
        super().__init__(coordinator, entry, subentry, ATTR_OVERSPEND_LIMIT_REACHED)

    @property
    def available(self) -> bool:
        """Report unavailable for a SIM that has no wallet in use.

        A never-topped-up wallet answers ``reached: true``, which would read as
        "you have hit your limit" when the truth is "you have no wallet".
        """
        return super().available and wallet_is_active(self.coordinator.data)

    @property
    def is_on(self) -> bool:
        """Return True when Mozillion reports the overspend limit as reached."""
        return bool(self.coordinator.data.get(ATTR_OVERSPEND_LIMIT_REACHED))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw wallet payload the flag comes from."""

        return {ATTR_WALLET: self.coordinator.data.get(ATTR_WALLET)}
