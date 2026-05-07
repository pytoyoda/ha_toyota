"""Tests for the _strip_route helper that slims sensor attribute payloads."""

from __future__ import annotations

from custom_components.toyota.sensor import _strip_route


def _trip_with_route(route_points: int) -> dict:
    return {
        "id": "abc-123",
        "start_ts": "2026-05-07T14:01:57+00:00",
        "end_ts": "2026-05-07T14:32:11+00:00",
        "stats": {"distance_m": 5257, "duration_s": 1814},
        "behaviours": [{"ts": "...", "type": "B"}],
        "scores": {"global": 73},
        "route": [
            {"lat": 47.0 + i / 1000.0, "lon": 20.0 + i / 1000.0}
            for i in range(route_points)
        ],
    }


def test_strip_route_removes_route_key():
    out = _strip_route(_trip_with_route(50))
    assert "route" not in out


def test_strip_route_replaces_with_count():
    out = _strip_route(_trip_with_route(2584))
    assert out["route_point_count"] == 2584


def test_strip_route_preserves_other_fields():
    src = _trip_with_route(10)
    out = _strip_route(src)
    for key in ("id", "start_ts", "end_ts", "stats", "behaviours", "scores"):
        assert out[key] == src[key]


def test_strip_route_handles_missing_route():
    src = {"id": "no-route", "stats": {"distance_m": 100}}
    out = _strip_route(src)
    assert out["route_point_count"] == 0
    assert out["id"] == "no-route"
    assert out["stats"] == {"distance_m": 100}


def test_strip_route_handles_none_route():
    src = {"id": "none-route", "route": None}
    out = _strip_route(src)
    assert out["route_point_count"] == 0
    assert "route" not in out


def test_strip_route_does_not_mutate_input():
    src = _trip_with_route(5)
    src_before = {**src, "route": list(src["route"])}
    _strip_route(src)
    assert src == src_before


def test_strip_route_payload_shrinks_dramatically():
    """Sanity check: stripping the route brings the payload size down by 100x+.

    Real-world example from the live RAV4 cache (2026-05-07): a single
    111 km trip was 261,788 bytes JSON with route polyline, ~28 bytes
    after stripping. This synthetic case mirrors the same shape with
    minimal route fields, so absolute numbers are smaller; the ratio is
    what we care about.
    """
    import json

    full = _trip_with_route(2500)
    full_size = len(json.dumps(full))
    slim_size = len(json.dumps(_strip_route(full)))
    assert full_size > slim_size * 100  # Order-of-magnitude shrinkage
