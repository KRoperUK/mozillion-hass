"""Tests that every translation file matches the source strings.

Translations are easy to leave half-finished: add a config-flow step, translate
English, forget the other languages. These checks make that a test failure
rather than a bug report from someone seeing an untranslated key.

Two kinds of check live here:

* **source integrity** — ``strings.json`` and the config flow agree, so a new
  form cannot ship without somewhere to translate it;
* **per-language** — every ``translations/<lang>.json`` has exactly the same
  keys as the source, with no blanks and no English left behind.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from custom_components.mozillion.config_flow import (
    MozillionConfigFlow,
    MozillionOptionsFlowHandler,
)
from custom_components.mozillion.sensor import DATA_SENSORS

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "mozillion"
)
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"
SOURCE = COMPONENT_DIR / "strings.json"

# Steps whose name is a delegation shim rather than a form the user sees: the
# form lives behind the matching `*_confirm` step, which is what gets translated.
NON_FORM_STEPS = {"import", "reauth", "reconfigure"}

LANGUAGES = sorted(path.stem for path in TRANSLATIONS_DIR.glob("*.json"))

# Strings that are legitimately the same in every language.
UNIVERSAL = {"config.step.user.title", "config.step.select_sim.data.sim"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    """Flatten nested dicts to dotted paths, keeping values."""
    flat: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat |= _flatten(value, path)
        else:
            flat[path] = value
    return flat


def _step_ids(flow: type) -> set[str]:
    """Return the form step ids a flow defines itself.

    ``vars()`` rather than ``dir()``: Home Assistant's base ``ConfigFlow``
    carries stubs for bluetooth/dhcp/ssdp/… discovery that this integration does
    not implement, so they must not read as missing translations.
    """
    names = {
        name[len("async_step_") :]
        for name in vars(flow)
        if name.startswith("async_step_")
    }
    return names - NON_FORM_STEPS


@pytest.fixture(scope="module")
def strings() -> dict[str, Any]:
    return _load(SOURCE)


# ---------------------------------------------------------------------------
# Source integrity
# ---------------------------------------------------------------------------


def test_translations_exist_alongside_a_source() -> None:
    assert SOURCE.is_file(), "strings.json is the translation source"
    assert "en" in LANGUAGES


def test_every_config_flow_form_is_translatable(strings) -> None:
    """A new form must have a translation entry, in every language."""
    config_forms = _step_ids(MozillionConfigFlow)
    config_steps = set(strings["config"]["step"])

    assert config_forms == config_steps, (
        f"flow/translation mismatch: forms={sorted(config_forms)} "
        f"translated={sorted(config_steps)}"
    )

    options_forms = _step_ids(MozillionOptionsFlowHandler)
    options_steps = set(strings["options"]["step"])
    assert options_forms == options_steps, (
        f"options flow/translation mismatch: forms={sorted(options_forms)} "
        f"translated={sorted(options_steps)}"
    )


def test_every_entity_is_translatable(strings) -> None:
    sensors = strings["entity"]["sensor"]
    for description in DATA_SENSORS:
        assert description.translation_key in sensors, (
            f"{description.key} has no translatable name"
        )
    assert "unlimited" in strings["entity"]["binary_sensor"]


def test_flow_reason_strings_are_translated(strings) -> None:
    """Abort and error reasons the flow can raise must have text."""
    config = strings["config"]
    assert {"reauth_successful", "already_configured"} <= set(config["abort"])
    assert {"cannot_connect", "missing_auth"} <= set(config["error"])


# ---------------------------------------------------------------------------
# Per-language
# ---------------------------------------------------------------------------


def test_english_mirrors_the_source_strings(strings) -> None:
    assert _load(TRANSLATIONS_DIR / "en.json") == strings


@pytest.mark.parametrize("language", LANGUAGES)
def test_language_matches_the_source_key_set(language: str, strings) -> None:
    """Every language must define exactly the same keys as strings.json."""
    source = _flatten(strings)
    translated = _flatten(_load(TRANSLATIONS_DIR / f"{language}.json"))

    missing = sorted(set(source) - set(translated))
    extra = sorted(set(translated) - set(source))

    assert not missing, f"{language}.json is missing: {missing}"
    assert not extra, f"{language}.json has unknown keys: {extra}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_language_has_no_blank_strings(language: str) -> None:
    for path, value in _flatten(_load(TRANSLATIONS_DIR / f"{language}.json")).items():
        assert value.strip(), f"{language}.json has a blank value at {path}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_language_is_actually_translated(language: str) -> None:
    """Catch a copy of en.json that was never translated."""
    if language == "en":
        pytest.skip("en.json is the source language")

    source = _flatten(_load(TRANSLATIONS_DIR / "en.json"))
    translated = _flatten(_load(TRANSLATIONS_DIR / f"{language}.json"))

    identical = {
        key for key, value in source.items() if translated[key] == value
    } - UNIVERSAL

    # A few strings are legitimately universal, but most of the file must differ.
    assert len(identical) < len(source) * 0.25, (
        f"{language}.json looks untranslated: {len(identical)}/{len(source)} "
        f"strings are still English ({sorted(identical)[:5]}…)"
    )
