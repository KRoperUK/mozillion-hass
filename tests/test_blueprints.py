"""Tests for the shipped automation blueprints.

These are YAML artefacts, so nothing else in the suite would notice a typo in a
selector or an `!input` that names an input the blueprint never declares. Parsing
them with Home Assistant's own loader and validating against the blueprint schema
catches both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from annotatedyaml.objects import Input
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.util.yaml import loader

BLUEPRINT_DIR = (
    Path(__file__).resolve().parent.parent / "blueprints" / "automation" / "mozillion"
)
BLUEPRINTS = sorted(BLUEPRINT_DIR.glob("*.yaml"))


def _load(path: Path) -> dict[str, Any]:
    """Load a blueprint with Home Assistant's YAML loader, which knows ``!input``."""

    return loader.load_yaml(str(path))


def _blueprint(path: Path) -> Blueprint:
    return Blueprint(
        _load(path),
        path=str(path),
        expected_domain="automation",
        schema=BLUEPRINT_SCHEMA,
    )


def test_blueprints_are_shipped() -> None:
    assert BLUEPRINTS, f"no blueprints found in {BLUEPRINT_DIR}"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_blueprint_is_valid(path: Path) -> None:
    """The schema check is HA's own, so this is what the UI would enforce."""
    blueprint = _blueprint(path)

    assert blueprint.name
    assert blueprint.metadata["domain"] == "automation"
    assert blueprint.inputs, "a blueprint with no inputs is not configurable"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_every_input_is_used_and_declared(path: Path) -> None:
    """An `!input` that names nothing, or an input nobody uses, is a bug.

    HA substitutes inputs by name, so a typo silently yields an empty value at
    runtime rather than an error.
    """
    data = _load(path)
    declared = set(data["blueprint"]["input"])
    used = _collect_inputs(data)

    assert used <= declared, f"{path.name} uses undeclared inputs: {used - declared}"
    assert declared <= used, f"{path.name} declares unused inputs: {declared - used}"


def _collect_inputs(node: Any) -> set[str]:
    """Walk the parsed document for ``!input`` markers.

    HA's loader turns ``!input name`` into an ``Input(name=...)`` object.
    """

    if isinstance(node, Input):
        return {node.name}
    found: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            found |= _collect_inputs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _collect_inputs(value)
    return found


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_blueprint_targets_mozillion_entities(path: Path) -> None:
    """Every entity selector should be scoped to this integration."""
    for name, spec in _load(path)["blueprint"]["input"].items():
        selector = spec.get("selector", {})
        if "entity" not in selector:
            continue
        assert selector["entity"].get("integration") == "mozillion", (
            f"{path.name}:{name} is not scoped to the mozillion integration"
        )
