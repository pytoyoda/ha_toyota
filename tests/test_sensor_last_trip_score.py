"""Tests for ``ToyotaLastTripScoreSensor`` (bugfix: was reading a non-existent
``scores["score"]`` key instead of ``scores["global"]``, always showing
"Unknown" in Home Assistant).

Also covers the ``extra_state_attributes`` fix: the per-category breakdown
(acceleration/braking/advice/constant speed) lives in the trip's ``scores``
dict (aliased ``constantSpeed``), not in ``behaviours`` (an unrelated list
of timestamped driving events).
"""

from __future__ import annotations

from custom_components.toyota.sensor_extra import ToyotaLastTripScoreSensor


def _make_sensor(last_trip: dict | None) -> ToyotaLastTripScoreSensor:
    sensor = ToyotaLastTripScoreSensor.__new__(ToyotaLastTripScoreSensor)
    sensor._last_trip = lambda: last_trip  # type: ignore[method-assign]
    return sensor


def test_native_value_reads_global_score():
    trip = {
        "scores": {
            "global": 83,
            "acceleration": 80,
            "braking": 80,
            "advice": 5,
            "constantSpeed": 30,
        }
    }
    sensor = _make_sensor(trip)
    assert sensor.native_value == 83


def test_native_value_none_when_no_trip():
    sensor = _make_sensor(None)
    assert sensor.native_value is None


def test_native_value_none_when_no_scores():
    sensor = _make_sensor({"start_ts": "2026-01-01T00:00:00+00:00"})
    assert sensor.native_value is None


def test_extra_state_attributes_reads_breakdown_from_scores():
    trip = {
        "start_ts": "2026-01-01T00:00:00+00:00",
        "end_ts": "2026-01-01T00:30:00+00:00",
        "stats": {"distance_m": 5000},
        "scores": {
            "global": 83,
            "acceleration": 80,
            "braking": 78,
            "advice": 5,
            "constantSpeed": 30,
        },
        # behaviours is a list of unrelated timestamped events - must not be
        # mistaken for the score breakdown.
        "behaviours": [{"ts": "2026-01-01T00:10:00+00:00", "type": "harsh_braking"}],
    }
    sensor = _make_sensor(trip)
    attrs = sensor.extra_state_attributes
    assert attrs["acceleration"] == 80
    assert attrs["braking"] == 78
    assert attrs["constant_speed"] == 30
    assert attrs["advice"] == 5
    assert attrs["score"] == 83
    assert attrs["distance_m"] == 5000


def test_extra_state_attributes_none_when_no_trip():
    sensor = _make_sensor(None)
    assert sensor.extra_state_attributes is None


def test_extra_state_attributes_omits_missing_score_fields():
    trip = {"start_ts": "2026-01-01T00:00:00+00:00", "stats": {}}
    sensor = _make_sensor(trip)
    attrs = sensor.extra_state_attributes
    assert "score" not in attrs
    assert "acceleration" not in attrs
