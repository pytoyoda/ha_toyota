"""Tests for RecentTripsManager (rolling-cache + delta-fetch orchestration)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.toyota.refresh_strategy import RefreshTrigger
from custom_components.toyota.trips_manager import RecentTripsManager


def _trip_dict(trip_id: str) -> dict:
    """A pytoyoda _TripModel-shaped dict (post .model_dump(by_alias=True))."""
    return {
        "id": trip_id,
        "summary": {
            "startTs": f"2026-04-26T07:{trip_id[-2:].zfill(2)}:00Z",
            "endTs": f"2026-04-26T07:{trip_id[-2:].zfill(2)}:30Z",
            "startLat": 47.1,
            "startLon": 20.2,
            "endLat": 47.2,
            "endLon": 20.3,
            "length": 1000,
            "duration": 600,
        },
        "hdc": None,
        "scores": None,
        "behaviours": None,
        "route": [],
    }


def _wrap_as_trip(trip_dict: dict):
    """Mimic pytoyoda's Trip wrapper: an object with `_trip` attr that has
    `model_dump(by_alias=True)`. Tests use SimpleNamespace stubs that return
    the dict directly when `model_dump` is called."""
    inner = MagicMock()
    inner.model_dump = MagicMock(return_value=trip_dict)
    wrapper = SimpleNamespace(_trip=inner)
    return wrapper


def _make_vehicle(trip_dicts: list[dict], alias: str = "RAV4"):
    """Build a Vehicle stub with `get_recent_trips` returning the supplied
    pre-shaped trip dicts wrapped as Trip-like objects."""
    captured: dict = {}

    async def fake_get_recent_trips(limit, with_route=False, **_):
        captured.update(limit=limit, with_route=with_route)
        return [_wrap_as_trip(t) for t in trip_dicts[:limit]]

    v = SimpleNamespace(
        alias=alias,
        get_recent_trips=AsyncMock(side_effect=fake_get_recent_trips),
        _captured=captured,
    )
    return v


def _make_decision(trigger: RefreshTrigger):
    """Lightweight stand-in for RefreshDecision; manager only reads .trigger."""
    return SimpleNamespace(trigger=trigger)


def _make_entry(entry_id: str = "test_entry"):
    return SimpleNamespace(entry_id=entry_id)


@pytest.mark.asyncio
async def test_disabled_when_max_zero(hass):
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=0)
    await mgr.async_setup()
    v = _make_vehicle([_trip_dict("t1")])
    await mgr.async_maybe_refresh(v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED))
    v.get_recent_trips.assert_not_called()
    assert mgr.cache.get("VIN1") == []


@pytest.mark.asyncio
async def test_cold_cache_seeds_with_max(hass):
    """First refresh on empty cache fetches limit=max regardless of trigger."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    v = _make_vehicle(
        [_trip_dict(f"t{i}") for i in range(10)],
    )
    await mgr.async_maybe_refresh(v, "VIN1", _make_decision(RefreshTrigger.NONE))
    v.get_recent_trips.assert_called_once()
    assert v._captured["limit"] == 5
    assert v._captured["with_route"] is True
    assert len(mgr.cache.get("VIN1")) == 5


@pytest.mark.asyncio
async def test_just_stopped_with_one_new_trip_appends(hass):
    """JUST_STOPPED + delta-fetch sees 1 new + 1 cached → append the new one."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    # Pre-seed with a trip
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    # Vehicle returns a NEW trip first, then the cached one (newest-first ordering)
    v = _make_vehicle([_trip_dict("brand-new"), _trip_dict("cached")])
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED)
    )
    cached = mgr.cache.get("VIN1")
    assert len(cached) == 2
    assert cached[0]["id"] == "brand-new"
    assert cached[1]["id"] == "cached"


@pytest.mark.asyncio
async def test_just_stopped_no_new_trip_no_op(hass):
    """JUST_STOPPED + delta-fetch sees only cached trips → no change, mark for followup."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    v = _make_vehicle([_trip_dict("cached")])
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED)
    )
    assert len(mgr.cache.get("VIN1")) == 1
    # Should have flagged followup pending
    assert mgr._followup_pending.get("VIN1") is True


@pytest.mark.asyncio
async def test_just_stopped_followup_only_runs_when_pending(hass):
    """JUST_STOPPED_FOLLOWUP without prior pending flag → no API call."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    v = _make_vehicle([_trip_dict("cached")])
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED_FOLLOWUP)
    )
    v.get_recent_trips.assert_not_called()


@pytest.mark.asyncio
async def test_followup_fires_when_pending_and_picks_up_late_trip(hass):
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    mgr._followup_pending["VIN1"] = True
    v = _make_vehicle([_trip_dict("late-arrival"), _trip_dict("cached")])
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED_FOLLOWUP)
    )
    cached = mgr.cache.get("VIN1")
    assert len(cached) == 2
    assert cached[0]["id"] == "late-arrival"
    # Followup pending should be cleared after success
    assert mgr._followup_pending.get("VIN1") is False


@pytest.mark.asyncio
async def test_gap_detected_triggers_full_refill(hass):
    """JUST_STOPPED + both delta trips are new → gap → fall back to full reseed."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("old")])
    # Vehicle returns 5 new trips for the gap-fill seed call (after delta fetch
    # of 2 returns 2 new and triggers refill).
    v = _make_vehicle(
        [
            _trip_dict("newest"),
            _trip_dict("newer"),
            _trip_dict("new"),
            _trip_dict("less-new"),
            _trip_dict("least-new"),
        ]
    )
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED)
    )
    # Should have called twice: limit=2 (delta), then limit=5 (full reseed)
    assert v.get_recent_trips.call_count == 2
    cached = mgr.cache.get("VIN1")
    assert len(cached) == 5
    assert cached[0]["id"] == "newest"


@pytest.mark.asyncio
async def test_other_triggers_are_noop(hass):
    """Triggers that aren't stop-related don't fetch."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    v = _make_vehicle([_trip_dict("brand-new")])
    for trigger in [
        RefreshTrigger.NONE,
        RefreshTrigger.IDLE_WAKE,
        RefreshTrigger.CACHE_STALE,
        RefreshTrigger.CURRENTLY_MOVING,
    ]:
        await mgr.async_maybe_refresh(v, "VIN1", _make_decision(trigger))
    v.get_recent_trips.assert_not_called()


@pytest.mark.asyncio
async def test_service_refresh_replaces_cache(hass):
    """Service call discards cache and refetches limit=N."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("a"), _trip_dict("b"), _trip_dict("c")])
    v = _make_vehicle([_trip_dict("fresh1"), _trip_dict("fresh2")])
    count = await mgr.async_service_refresh("VIN1", v, limit=2)
    assert count == 2
    cached = mgr.cache.get("VIN1")
    assert [t["id"] for t in cached] == ["fresh1", "fresh2"]


@pytest.mark.asyncio
async def test_service_refresh_works_when_max_zero(hass):
    """Service call works regardless of max_recent_trips config."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=0)
    await mgr.async_setup()
    v = _make_vehicle([_trip_dict("a"), _trip_dict("b"), _trip_dict("c")])
    count = await mgr.async_service_refresh("VIN1", v, limit=3)
    assert count == 3


@pytest.mark.asyncio
async def test_service_refresh_rejects_invalid_limit(hass):
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    v = _make_vehicle([])
    with pytest.raises(ValueError, match="limit must be between 1 and 50"):
        await mgr.async_service_refresh("VIN1", v, limit=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 50"):
        await mgr.async_service_refresh("VIN1", v, limit=51)


@pytest.mark.asyncio
async def test_update_max_lowered_trims(hass):
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=10)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict(f"t{i}") for i in range(8)])
    mutated = mgr.update_max(3)
    assert mutated is True
    assert len(mgr.cache.get("VIN1")) == 3
    assert mgr.max_recent_trips == 3


@pytest.mark.asyncio
async def test_update_max_raised_no_mutation(hass):
    """Raising max doesn't trim or refetch; the cold-start branch handles fill."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=3)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict(f"t{i}") for i in range(3)])
    mutated = mgr.update_max(10)
    assert mutated is False
    assert len(mgr.cache.get("VIN1")) == 3
    assert mgr.max_recent_trips == 10


@pytest.mark.asyncio
async def test_update_max_to_zero_drops_entries(hass):
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("a"), _trip_dict("b")])
    mgr.cache.set("VIN2", [_trip_dict("c")])
    mutated = mgr.update_max(0)
    assert mutated is True
    assert mgr.cache.get("VIN1") == []
    assert mgr.cache.get("VIN2") == []


@pytest.mark.asyncio
async def test_update_max_unchanged_no_mutation(hass):
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("a")])
    mutated = mgr.update_max(5)
    assert mutated is False
    assert len(mgr.cache.get("VIN1")) == 1


@pytest.mark.asyncio
async def test_setup_trims_to_max_on_load(hass):
    """Cache loaded from disk with more trips than the new max gets trimmed.

    Covers the options-flow-lowered-max + reload sequence: fresh manager
    with new (lower) max loads the on-disk cache that was written with the
    old (higher) max, and trims back to the new ceiling on async_setup.
    """
    # Pre-seed the on-disk cache with 10 trips via a manager configured
    # at max=10, then save.
    seed_mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=10)
    await seed_mgr.async_setup()
    seed_mgr.cache.set("VIN1", [_trip_dict(f"t{i}") for i in range(10)])
    await seed_mgr.cache.save()

    # New manager with lower max picks up the 10-trip cache and trims to 4.
    new_mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=4)
    await new_mgr.async_setup()
    assert len(new_mgr.cache.get("VIN1")) == 4


@pytest.mark.asyncio
async def test_underfilled_on_load_seeds_once(hass):
    """Cache loaded with fewer trips than the new max triggers a one-shot seed.

    Covers the options-flow-raised-max + reload sequence: fresh manager with
    new (higher) max loads an on-disk cache written at the old (lower) max.
    The first refresh tick should seed up to the new ceiling, regardless of
    trigger. Subsequent ticks fall through to normal delta-fetch behaviour.
    """
    # Pre-seed disk with 5 trips via a manager configured at max=5.
    seed_mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await seed_mgr.async_setup()
    seed_mgr.cache.set("VIN1", [_trip_dict(f"old{i}") for i in range(5)])
    await seed_mgr.cache.save()

    # New manager with raised max picks up the 5-trip cache.
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=10)
    await mgr.async_setup()
    assert len(mgr.cache.get("VIN1")) == 5  # not trimmed (already <= max)
    assert "VIN1" in mgr._underfilled_vins

    # First refresh tick should seed with limit=max=10, regardless of trigger.
    v = _make_vehicle([_trip_dict(f"fresh{i}") for i in range(10)])
    await mgr.async_maybe_refresh(v, "VIN1", _make_decision(RefreshTrigger.NONE))
    v.get_recent_trips.assert_called_once()
    assert v._captured["limit"] == 10
    assert len(mgr.cache.get("VIN1")) == 10
    # Underfill flag should be cleared after one-shot refill.
    assert "VIN1" not in mgr._underfilled_vins

    # Subsequent NONE-trigger tick should NOT call the API (steady state).
    v.get_recent_trips.reset_mock()
    await mgr.async_maybe_refresh(v, "VIN1", _make_decision(RefreshTrigger.NONE))
    v.get_recent_trips.assert_not_called()


@pytest.mark.asyncio
async def test_seed_failure_leaves_cache_empty(hass):
    """If get_recent_trips raises, cache stays empty rather than partial."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    v = SimpleNamespace(
        alias="RAV4",
        get_recent_trips=AsyncMock(side_effect=Exception("API down")),
    )
    await mgr.async_maybe_refresh(v, "VIN1", _make_decision(RefreshTrigger.NONE))
    assert mgr.cache.get("VIN1") == []
