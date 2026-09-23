"""Tests that our overrides match the way Home Assistant declares them.

Two bugs in one day were the same shape: a hook declared differently from how HA
declares it. ``async_get_supported_subentry_types`` was an instance method where
HA uses a ``classmethod``, so HA's own call -- on the class, from ``HANDLERS`` --
raised instead of returning the supported subentry types.

Nothing else in the suite would notice that: it only breaks when HA calls the
hook the way HA calls it. This compares each override against the declaration it
is overriding, so the mismatch fails here instead.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

import pytest
from custom_components.mozillion import binary_sensor as bs
from custom_components.mozillion import config_flow as cf
from custom_components.mozillion import coordinator as co
from custom_components.mozillion import entity as en
from custom_components.mozillion import sensor as se

CLASSES = [
    cf.MozillionConfigFlow,
    cf.MozillionOptionsFlowHandler,
    cf.SimSubentryFlowHandler,
    co.MozillionCoordinator,
    en.MozillionEntity,
    se.MozillionSensor,
    bs.MozillionUnlimitedSensor,
]

# `property` and `cached_property` are deliberately interchangeable here. HA
# declares `device_info` and `native_value` as cached_property; overriding them
# with a plain property is not just legal but required for anything that changes
# with each poll, because a cached_property would freeze the first value read and
# serve it forever. Treating those as equivalent keeps this check about the
# distinction that actually breaks -- classmethod/staticmethod versus instance.
EQUIVALENT_KINDS = {frozenset({"property", "cached_property"})}


def _kind(member: Any) -> str:
    if isinstance(member, classmethod):
        return "classmethod"
    if isinstance(member, staticmethod):
        return "staticmethod"
    if isinstance(member, functools.cached_property):
        return "cached_property"
    if isinstance(member, property):
        return "property"
    if inspect.isfunction(member):
        return "instance"
    return type(member).__name__


def _comparable(ours: str, theirs: str) -> bool:
    return ours == theirs or frozenset({ours, theirs}) in EQUIVALENT_KINDS


def _overrides() -> list[tuple[type, str, Any, type, Any]]:
    """Yield (our class, name, our member, HA base, HA member) for each override."""

    found = []
    for cls in CLASSES:
        for name, member in vars(cls).items():
            if name.startswith("__"):
                continue
            for base in cls.__mro__[1:]:
                if name in vars(base):
                    found.append((cls, name, member, base, vars(base)[name]))
                    break
    return found


def test_the_audit_actually_sees_overrides() -> None:
    """Guard the guard: an empty sweep would pass vacuously."""
    assert len(_overrides()) > 15


@pytest.mark.parametrize(
    ("cls", "name", "member", "base", "ha_member"),
    _overrides(),
    ids=lambda value: getattr(value, "__name__", str(value))[:40],
)
def test_override_kind_matches_home_assistant(
    cls: type, name: str, member: Any, base: type, ha_member: Any
) -> None:
    ours, theirs = _kind(member), _kind(ha_member)
    assert _comparable(ours, theirs), (
        f"{cls.__name__}.{name} is declared {ours}, but "
        f"{base.__module__}.{base.__qualname__} declares {theirs}"
    )


def test_the_subentry_types_hook_is_a_classmethod() -> None:
    """Named explicitly because this is the bug that motivated the file.

    Home Assistant calls it on the class, so an instance method binds the config
    entry to ``self`` and the call fails.
    """
    assert isinstance(
        vars(cf.MozillionConfigFlow)["async_get_supported_subentry_types"],
        classmethod,
    )


def test_the_options_flow_hook_is_a_staticmethod() -> None:
    """Same reasoning: HA calls it on the class, with the entry as an argument."""
    assert isinstance(
        vars(cf.MozillionConfigFlow)["async_get_options_flow"], staticmethod
    )
