"""Tests for ``format_statistics_attributes`` (pytoyoda/ha_toyota#93).

Covers the new ``Hybrid_score`` attribute surfaced on the day/week/month/year
stats sensors, sourced from ``Summary.hybrid_score``.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from custom_components.toyota.utils import format_statistics_attributes


def _make_statistics(hybrid_score: float | None) -> SimpleNamespace:
    """Build a duck-typed stand-in for pytoyoda's ``Summary``."""
    return SimpleNamespace(
        average_speed=42.0,
        countries=["GB"],
        duration=timedelta(minutes=30),
        fuel_consumed=5.0,
        average_fuel_consumed=6.5,
        ev_distance=None,
        ev_duration=None,
        from_date=date(2024, 1, 1),
        to_date=date(2024, 1, 31),
        hybrid_score=hybrid_score,
    )


def _make_vehicle_info() -> SimpleNamespace:
    return SimpleNamespace(fuel_type=None, extended_capabilities=None)


def test_hybrid_score_present_is_rounded():
    stats = _make_statistics(83.456)
    attrs = format_statistics_attributes(stats, _make_vehicle_info())
    assert attrs["Hybrid_score"] == 83.5


def test_hybrid_score_none_when_not_reported():
    stats = _make_statistics(None)
    attrs = format_statistics_attributes(stats, _make_vehicle_info())
    assert attrs["Hybrid_score"] is None


def test_hybrid_score_zero_is_preserved_not_treated_as_falsy():
    # 0.0 is a legitimate (if unlikely) score and must not collapse to None.
    stats = _make_statistics(0.0)
    attrs = format_statistics_attributes(stats, _make_vehicle_info())
    assert attrs["Hybrid_score"] == 0.0
