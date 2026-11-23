"""Compatibility shims for the range of Home Assistant versions we support."""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

# Home Assistant 2026.10 replaced voluptuous with probatio for flow schemas.
# hacs.json still declares 2026.6 as the minimum, so pick whichever the running
# core provides rather than requiring the newest release. Every module that builds
# a flow schema imports this one name, so the choice is made in one place.
schema: ModuleType = importlib.import_module(
    "probatio" if importlib.util.find_spec("probatio") else "voluptuous"
)
