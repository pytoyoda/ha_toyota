"""Transform pytoyoda's _TripModel dicts to the journey-viewer-card Trip shape.

pytoyoda exposes trips through Pydantic-aliased camelCase keys (Toyota's
native shape: ``startTs``, ``startLat``, ``averageSpeed`` etc.). The
journey-viewer-card data contract is source-agnostic snake_case
(``start_ts``, ``stats.average_speed_kmh`` etc.). This module bridges the
two for Toyota source.

Lifted with minor adjustments from
``endpoint-exploration/fetch-trips.py:_to_card_shape``. Differences:

- Drops absent optional fields (None-valued) entirely, per the multi-source
  design (each source's normaliser only writes fields its source has).
- Returns immediately on missing summary - the trip can't be rendered
  meaningfully without start/end.

Pure function, no I/O. Tested in tests/test_trips_transform.py.
"""

from __future__ import annotations

_ROUTE_OPTIONAL_KEYS = ("overspeed", "highway", "mode", "isEv")

_STATS_KEYS_FROM_SUMMARY: dict[str, str] = {
    # card_key -> Toyota summary key
    "distance_m": "length",
    "duration_s": "duration",
    "duration_idle_s": "durationIdle",
    "max_speed_kmh": "maxSpeed",
    "average_speed_kmh": "averageSpeed",
    "fuel_consumption_ml": "fuelConsumption",
    "length_overspeed_m": "lengthOverspeed",
    "duration_overspeed_s": "durationOverspeed",
    "length_highway_m": "lengthHighway",
    "duration_highway_s": "durationHighway",
    "countries": "countries",
    "night_trip": "nightTrip",
}

_STATS_KEYS_FROM_HDC: dict[str, str] = {
    # card_key -> Toyota hdc key (PHEV-only; absent on AYGO)
    "ev_time_s": "evTime",
    "ev_distance_m": "evDistance",
    "charge_time_s": "chargeTime",
    "charge_distance_m": "chargeDist",
    "eco_time_s": "ecoTime",
    "eco_distance_m": "ecoDist",
    "power_time_s": "powerTime",
    "power_distance_m": "powerDist",
}


def _coerce_route(route_in: list[dict]) -> list[dict]:
    """Map Toyota route points to card RoutePoints, dropping the ones missing GPS.

    Per route point we keep ``lat``/``lon`` always, plus any of
    ``overspeed``/``highway``/``mode``/``isEv`` that's present (None values
    are dropped). The card consumer treats absent flags the same as None.
    """
    out: list[dict] = []
    for pt in route_in or []:
        lat = pt.get("lat")
        lon = pt.get("lon")
        if lat is None or lon is None:
            continue
        item: dict = {"lat": lat, "lon": lon}
        for opt in _ROUTE_OPTIONAL_KEYS:
            v = pt.get(opt)
            if v is not None:
                item[opt] = v
        out.append(item)
    return out


def _build_stats(summary: dict, hdc: dict | None) -> dict:
    """Build the stats dict, omitting keys whose source value is None.

    Per Q3 of the recent-trips design interview: drop absent fields rather
    than write None. Multi-source rationale - each source's normaliser only
    writes fields its source has.
    """
    stats: dict = {}
    for card_key, src_key in _STATS_KEYS_FROM_SUMMARY.items():
        v = summary.get(src_key)
        if v is not None:
            stats[card_key] = v
    if hdc:
        for card_key, src_key in _STATS_KEYS_FROM_HDC.items():
            v = hdc.get(src_key)
            if v is not None:
                stats[card_key] = v
    return stats


def to_card_shape(trip_dict: dict, vehicle_alias: str | None) -> dict | None:
    """Map a pytoyoda _TripModel dict to the journey-viewer-card Trip shape.

    Returns None if the trip has no summary - we can't render a trip with
    no start/end timestamps or coordinates.

    Args:
        trip_dict: Result of ``_TripModel.model_dump(by_alias=True)`` or the
            equivalent raw API response trip object.
        vehicle_alias: For the ``source`` field on the resulting Trip.
            Defaults to ``"toyota"`` when None.

    Returns:
        A dict matching the ``Trip`` interface in
        ``journey-viewer-card/src/types.ts``, or None if untransformable.
    """
    summary = trip_dict.get("summary") or {}
    if not summary:
        return None

    hdc = trip_dict.get("hdc")
    scores = trip_dict.get("scores")
    behaviours = trip_dict.get("behaviours")
    route_in = trip_dict.get("route") or []

    out: dict = {
        "id": trip_dict.get("id"),
        "source": (vehicle_alias or "toyota").lower(),
        "activity_type": "drive",
        "start_ts": summary.get("startTs"),
        "end_ts": summary.get("endTs"),
        "start": {"lat": summary.get("startLat"), "lon": summary.get("startLon")},
        "end": {"lat": summary.get("endLat"), "lon": summary.get("endLon")},
        "route": _coerce_route(route_in),
        "stats": _build_stats(summary, hdc if isinstance(hdc, dict) else None),
    }

    # Drop the trip-level optional fields when absent (Q3).
    if scores:
        out["scores"] = scores
    if behaviours:
        out["behaviours"] = behaviours

    return out
