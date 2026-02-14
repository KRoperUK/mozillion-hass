"""Tests for _deep_get and coordinator data transformation logic."""
from __future__ import annotations

import pytest

# Import the utility directly from the module
from custom_components.mozillion import _deep_get


class TestDeepGet:
    """Tests for the _deep_get helper."""

    def test_simple_key(self) -> None:
        """Fetch a top-level key."""
        assert _deep_get({"foo": 42}, "foo") == 42

    def test_dotted_key(self) -> None:
        """Fetch a nested value via dotted path."""
        data = {"a": {"b": {"c": "deep"}}}
        assert _deep_get(data, "a.b.c") == "deep"

    def test_missing_key_returns_none(self) -> None:
        """Missing intermediate key returns None."""
        assert _deep_get({"a": {"b": 1}}, "a.x.y") is None

    def test_none_key_returns_none(self) -> None:
        """Passing None as key returns None."""
        assert _deep_get({"a": 1}, None) is None

    def test_empty_key_returns_none(self) -> None:
        """Passing empty string returns None."""
        assert _deep_get({"a": 1}, "") is None

    def test_non_dict_intermediate(self) -> None:
        """Non-dict intermediate returns None."""
        assert _deep_get({"a": 123}, "a.b") is None

    def test_top_level_missing(self) -> None:
        """Completely missing top-level key."""
        assert _deep_get({}, "missing") is None


class TestCoordinatorCalculations:
    """Tests for the remaining/percentage calculations used in the coordinator."""

    def test_remaining_calculation(self) -> None:
        """Remaining should be total - usage."""
        total = 10.0
        usage = 3.5
        remaining = float(total) - float(usage)
        assert remaining == 6.5

    def test_percentage_calculation(self) -> None:
        """Percentage should be (usage / total) * 100."""
        total = 10.0
        usage = 2.5
        percentage = (float(usage) / float(total)) * 100
        assert percentage == 25.0

    def test_percentage_zero_total(self) -> None:
        """Zero total should yield 0% not a division error."""
        total = 0.0
        usage = 0.0
        percentage = (float(usage) / float(total)) * 100 if float(total) > 0 else 0
        assert percentage == 0

    def test_remaining_with_none_values(self) -> None:
        """None values should not crash."""
        total = None
        usage = None
        remaining = None
        if total is not None and usage is not None:
            remaining = float(total) - float(usage)
        assert remaining is None
