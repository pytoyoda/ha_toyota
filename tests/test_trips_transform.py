"""Tests for trips_transform.to_card_shape (pytoyoda _TripModel -> card Trip)."""

from __future__ import annotations

import pytest

from custom_components.toyota.trips_transform import to_card_shape


def _full_trip_dict() -> dict:
    """A complete RAV4 PHEV trip with summary, hdc, scores, behaviours, route."""
    return {
        "id": "trip-uuid-1",
        "summary": {
            "startTs": "2026-04-26T07:06:45Z",
            "endTs": "2026-04-26T07:15:54Z",
            "startLat": 47.1652,
            "startLon": 20.2178,
            "endLat": 47.1927,
            "endLon": 20.1938,
            "length": 4943,
            "duration": 549,
            "durationIdle": 30,
            "maxSpeed": 67,
            "averageSpeed": 32.4,
            "fuelConsumption": 275,
            "lengthOverspeed": 0,
            "durationOverspeed": 0,
            "lengthHighway": 0,
            "durationHighway": 0,
            "countries": ["HU"],
            "nightTrip": False,
        },
        "hdc": {
            "evTime": 291,
            "evDistance": 2379,
            "chargeTime": 0,
            "chargeDist": 0,
            "ecoTime": 0,
            "ecoDist": 0,
            "powerTime": 0,
            "powerDist": 0,
        },
        "scores": {"acceleration": 90, "braking": 71, "global": 81},
        "behaviours": [
            {"lat": 47.1642, "lon": 20.2029, "type": "A", "good": True}
        ],
        "route": [
            {"lat": 47.1652, "lon": 20.2178, "isEv": True, "overspeed": False},
            {"lat": 47.1927, "lon": 20.1938, "isEv": False, "overspeed": False},
        ],
    }


def _aygo_trip_dict() -> dict:
    """ICE-only (no hdc, no scores, no behaviours) - mirrors AYGO shape."""
    return {
        "id": "trip-uuid-2",
        "summary": {
            "startTs": "2026-04-26T08:00:00Z",
            "endTs": "2026-04-26T08:10:00Z",
            "startLat": 47.5,
            "startLon": 19.0,
            "endLat": 47.6,
            "endLon": 19.1,
            "length": 5000,
            "duration": 600,
        },
        "hdc": None,
        "scores": None,
        "behaviours": None,
        "route": [
            {"lat": 47.5, "lon": 19.0},
            {"lat": 47.6, "lon": 19.1},
        ],
    }


def test_basic_top_level_fields():
    out = to_card_shape(_full_trip_dict(), "RAV4")
    assert out["id"] == "trip-uuid-1"
    assert out["source"] == "rav4"
    assert out["activity_type"] == "drive"
    assert out["start_ts"] == "2026-04-26T07:06:45Z"
    assert out["end_ts"] == "2026-04-26T07:15:54Z"


def test_start_end_coords():
    out = to_card_shape(_full_trip_dict(), "RAV4")
    assert out["start"] == {"lat": 47.1652, "lon": 20.2178}
    assert out["end"] == {"lat": 47.1927, "lon": 20.1938}


def test_route_keeps_optional_flags():
    out = to_card_shape(_full_trip_dict(), "RAV4")
    assert len(out["route"]) == 2
    assert out["route"][0] == {
        "lat": 47.1652, "lon": 20.2178, "isEv": True, "overspeed": False,
    }


def test_route_drops_points_missing_lat_or_lon():
    trip = _aygo_trip_dict()
    trip["route"] = [
        {"lat": 47.5, "lon": 19.0},
        {"lat": None, "lon": 19.1},  # bad
        {"lat": 47.6, "lon": None},  # bad
        {"lat": 47.6, "lon": 19.1},
    ]
    out = to_card_shape(trip, "AYGO")
    assert len(out["route"]) == 2
    assert all("lat" in p and "lon" in p for p in out["route"])


def test_stats_includes_phev_fields_for_rav4():
    out = to_card_shape(_full_trip_dict(), "RAV4")
    s = out["stats"]
    assert s["distance_m"] == 4943
    assert s["duration_s"] == 549
    assert s["max_speed_kmh"] == 67
    assert s["average_speed_kmh"] == 32.4
    assert s["fuel_consumption_ml"] == 275
    assert s["ev_time_s"] == 291
    assert s["ev_distance_m"] == 2379


def test_stats_drops_phev_fields_for_aygo():
    """Q3 acceptance: hdc=None means no ev_*/charge_*/eco_*/power_* keys."""
    out = to_card_shape(_aygo_trip_dict(), "AYGO")
    s = out["stats"]
    assert "ev_time_s" not in s
    assert "ev_distance_m" not in s
    assert "charge_time_s" not in s
    assert "eco_time_s" not in s
    assert "power_time_s" not in s
    # Summary fields still present
    assert s["distance_m"] == 5000
    assert s["duration_s"] == 600


def test_stats_drops_summary_fields_when_none():
    """Toyota sometimes returns partial summary; missing fields not set to None."""
    trip = _aygo_trip_dict()
    trip["summary"]["fuelConsumption"] = None  # explicitly None
    # maxSpeed is absent from the dict entirely
    out = to_card_shape(trip, "AYGO")
    s = out["stats"]
    assert "fuel_consumption_ml" not in s
    assert "max_speed_kmh" not in s


def test_scores_dropped_when_absent():
    """Q3: scores=None means the trip dict has no 'scores' key."""
    out = to_card_shape(_aygo_trip_dict(), "AYGO")
    assert "scores" not in out


def test_scores_included_when_present():
    out = to_card_shape(_full_trip_dict(), "RAV4")
    assert out["scores"] == {"acceleration": 90, "braking": 71, "global": 81}


def test_behaviours_dropped_when_absent():
    out = to_card_shape(_aygo_trip_dict(), "AYGO")
    assert "behaviours" not in out


def test_behaviours_included_when_present():
    out = to_card_shape(_full_trip_dict(), "RAV4")
    assert len(out["behaviours"]) == 1
    assert out["behaviours"][0]["type"] == "A"


def test_returns_none_when_summary_missing():
    """Defensive: untransformable trip yields None, caller decides what to do."""
    trip = {"id": "trip-uuid-3", "summary": None}
    assert to_card_shape(trip, "RAV4") is None
    trip2 = {"id": "trip-uuid-4"}  # no summary key at all
    assert to_card_shape(trip2, "RAV4") is None


def test_empty_route_yields_empty_route_list():
    trip = _aygo_trip_dict()
    trip["route"] = []
    out = to_card_shape(trip, "AYGO")
    assert out["route"] == []


def test_route_none_yields_empty_route_list():
    trip = _aygo_trip_dict()
    trip["route"] = None
    out = to_card_shape(trip, "AYGO")
    assert out["route"] == []


def test_alias_lowercased_for_source():
    out = to_card_shape(_aygo_trip_dict(), "MyRav4")
    assert out["source"] == "myrav4"


def test_alias_none_defaults_to_toyota():
    out = to_card_shape(_aygo_trip_dict(), None)
    assert out["source"] == "toyota"


def test_route_optional_flags_with_none_dropped():
    """If a route point has explicit None for an optional flag, drop it."""
    trip = _aygo_trip_dict()
    trip["route"] = [
        {"lat": 47.5, "lon": 19.0, "isEv": None, "overspeed": True},
    ]
    out = to_card_shape(trip, "AYGO")
    pt = out["route"][0]
    assert "isEv" not in pt
    assert pt["overspeed"] is True
