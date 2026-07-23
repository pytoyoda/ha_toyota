"""Tests for RecentTripsManager (rolling-cache + delta-fetch orchestration)."""

from __future__ import annotations

import asyncio
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
    `model_dump(by_alias=True)` and an `id` matching the dict's id field.
    """
    inner = MagicMock()
    inner.model_dump = MagicMock(return_value=trip_dict)
    inner.id = trip_dict.get("id")
    wrapper = SimpleNamespace(_trip=inner)
    return wrapper


def _make_vehicle(
    trip_dicts: list[dict],
    alias: str = "RAV4",
    *,
    trip_history=None,
):
    """Build a Vehicle stub with `get_recent_trips` returning the supplied
    pre-shaped trip dicts wrapped as Trip-like objects.

    By default ``trip_history`` mirrors ``trip_dicts[:1]`` wrapped, matching
    the production shape where pytoyoda's per-cycle trip_history endpoint
    returns the most recent trip with summary=True/route=False. Pass
    ``trip_history=...`` to override (e.g. ``[]`` for the AYGO no-trips
    path, ``None`` for the endpoint-failed path, or a custom Trip wrapper
    list to model id mismatch with the cache).
    """
    captured: dict = {}

    async def fake_get_recent_trips(limit, with_route=False, **_):
        captured.update(limit=limit, with_route=with_route)
        return [_wrap_as_trip(t) for t in trip_dicts[:limit]]

    if trip_history is None and trip_dicts:
        trip_history = [_wrap_as_trip(trip_dicts[0])]
    elif trip_history is None:
        trip_history = []

    v = SimpleNamespace(
        alias=alias,
        get_recent_trips=AsyncMock(side_effect=fake_get_recent_trips),
        trip_history=trip_history,
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
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED),
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
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED),
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
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED_FOLLOWUP),
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
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED_FOLLOWUP),
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
        ],
    )
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED),
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
async def test_underfilled_flag_survives_failed_refill(hass):
    """A failed one-shot refill keeps the underfill flag armed for retry."""
    seed_mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await seed_mgr.async_setup()
    seed_mgr.cache.set("VIN1", [_trip_dict(f"old{i}") for i in range(5)])
    await seed_mgr.cache.save()

    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=10)
    await mgr.async_setup()
    assert "VIN1" in mgr._underfilled_vins

    # Refill attempt fails: flag must stay armed, cache untouched.
    bad = SimpleNamespace(
        alias="RAV4",
        get_recent_trips=AsyncMock(side_effect=Exception("API down")),
        trip_history=[_wrap_as_trip(_trip_dict("old0"))],
    )
    await mgr.async_maybe_refresh(bad, "VIN1", _make_decision(RefreshTrigger.NONE))
    assert "VIN1" in mgr._underfilled_vins
    assert len(mgr.cache.get("VIN1")) == 5

    # Next cycle succeeds: refill lands, flag clears.
    good = _make_vehicle([_trip_dict(f"fresh{i}") for i in range(10)])
    await mgr.async_maybe_refresh(good, "VIN1", _make_decision(RefreshTrigger.NONE))
    assert "VIN1" not in mgr._underfilled_vins
    assert len(mgr.cache.get("VIN1")) == 10


@pytest.mark.asyncio
async def test_seed_failure_leaves_cache_empty(hass):
    """If get_recent_trips raises, cache stays empty rather than partial."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    # trip_history non-empty so piggyback gate doesn't short-circuit;
    # we want to reach get_recent_trips and have it raise.
    v = SimpleNamespace(
        alias="RAV4",
        get_recent_trips=AsyncMock(side_effect=Exception("API down")),
        trip_history=[_wrap_as_trip(_trip_dict("would-have-fetched"))],
    )
    await mgr.async_maybe_refresh(v, "VIN1", _make_decision(RefreshTrigger.NONE))
    assert mgr.cache.get("VIN1") == []


@pytest.mark.asyncio
async def test_service_refresh_failure_preserves_prior_cache(hass):
    """Service-call fetch failure must not nuke the existing cache."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("kept1"), _trip_dict("kept2")])
    v = SimpleNamespace(
        alias="RAV4",
        get_recent_trips=AsyncMock(side_effect=Exception("API down")),
    )
    count = await mgr.async_service_refresh("VIN1", v, limit=5)
    assert count == 2
    assert [t["id"] for t in mgr.cache.get("VIN1")] == ["kept1", "kept2"]


@pytest.mark.asyncio
async def test_concurrent_service_and_tick_serialise(hass):
    """Service call and coordinator tick on the same VIN must serialise.

    Without the per-VIN lock the cold-start branch in async_maybe_refresh sees
    the cache cleared by async_service_refresh and issues a duplicate fetch.
    """
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()

    call_count = 0
    fetch_started = asyncio.Event()
    fetch_release = asyncio.Event()

    async def slow_fetch(limit, with_route=False, **_):
        nonlocal call_count
        call_count += 1
        fetch_started.set()
        await fetch_release.wait()
        return [_wrap_as_trip(_trip_dict(f"t{i}")) for i in range(limit)]

    # trip_history shows a NEW trip (not in the cache the service call seeds),
    # so the tick's piggyback gate routes through the delta-fetch path rather
    # than skipping. Without this the tick would short-circuit and we'd lose
    # the lock-serialisation assertion's signal.
    v = SimpleNamespace(
        alias="RAV4",
        get_recent_trips=AsyncMock(side_effect=slow_fetch),
        trip_history=[_wrap_as_trip(_trip_dict("brand-new-after-service"))],
    )

    service_task = asyncio.create_task(mgr.async_service_refresh("VIN1", v, limit=5))
    await fetch_started.wait()
    # Service call holds the lock + is mid-fetch. Coordinator tick fires now.
    tick_task = asyncio.create_task(
        mgr.async_maybe_refresh(v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED)),
    )
    # Give the tick a chance to run; it should be blocked on the lock.
    await asyncio.sleep(0)
    fetch_release.set()
    await asyncio.gather(service_task, tick_task)

    # Service did one fetch. Tick saw a populated cache (via the lock) and
    # took the steady-state JUST_STOPPED -> delta-fetch path: one more call.
    # Pre-fix this would be 3 calls (service + cold-start tick + delta) or
    # corrupted state.
    assert call_count == 2


@pytest.mark.asyncio
async def test_async_prune_orphans_drops_unknown_vins(hass):
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN_KEEP", [_trip_dict("a")])
    mgr.cache.set("VIN_GONE", [_trip_dict("b")])
    mutated = await mgr.async_prune_orphans(["VIN_KEEP"])
    assert mutated is True
    assert mgr.cache.get("VIN_KEEP") == [_trip_dict("a")]
    assert mgr.cache.get("VIN_GONE") == []
    # Idempotent.
    mutated2 = await mgr.async_prune_orphans(["VIN_KEEP"])
    assert mutated2 is False


# ---------------------------------------------------------------------------
# Piggyback gating tests
# ---------------------------------------------------------------------------
# These exercise the cycle's free `vehicle.trip_history` signal as a gate
# on the heavier `get_recent_trips(with_route=True)` fetch. See
# trips_manager.async_maybe_refresh docstring + 2026-05-07 journal.


@pytest.mark.asyncio
async def test_piggyback_empty_trip_history_skips_fetch(hass):
    """AYGO path: vehicle has no trips data → skip fetch entirely."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    v = _make_vehicle([], trip_history=[])
    await mgr.async_maybe_refresh(
        v, "VIN_AYGO", _make_decision(RefreshTrigger.JUST_STOPPED),
    )
    v.get_recent_trips.assert_not_called()
    assert mgr.cache.get("VIN_AYGO") == []


@pytest.mark.asyncio
async def test_piggyback_none_trip_history_skips_fetch(hass):
    """trip_history endpoint failed this cycle → conservative skip."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    v = _make_vehicle([_trip_dict("t1")], trip_history=None)
    # The _make_vehicle helper synthesises a trip_history when
    # trip_history=None is passed AND trip_dicts is non-empty. Override
    # here to force the "endpoint failed" shape.
    v.trip_history = None
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED),
    )
    v.get_recent_trips.assert_not_called()


@pytest.mark.asyncio
async def test_piggyback_matching_id_skips_delta_and_arms_followup(hass):
    """Cache top id == trip_history top id on JUST_STOPPED → skip + flag followup.

    Toyota's summary endpoint may also be lagging behind a freshly-driven
    trip. Setting followup_pending lets the next stop tick reach the
    delta-fetch path if a new trip eventually surfaces.
    """
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    v = _make_vehicle([_trip_dict("cached")])  # default trip_history → cached
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED),
    )
    v.get_recent_trips.assert_not_called()
    assert mgr._followup_pending.get("VIN1") is True


@pytest.mark.asyncio
async def test_piggyback_matching_id_on_non_stop_does_not_arm_followup(hass):
    """Steady-state matching id should NOT arm followup-pending."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    v = _make_vehicle([_trip_dict("cached")])
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.NONE),
    )
    v.get_recent_trips.assert_not_called()
    assert mgr._followup_pending.get("VIN1") in (None, False)


@pytest.mark.asyncio
async def test_piggyback_new_id_triggers_delta_on_stop(hass):
    """trip_history shows a new trip → delta-fetch fires on JUST_STOPPED."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [_trip_dict("cached")])
    v = _make_vehicle(
        [_trip_dict("brand-new"), _trip_dict("cached")],
        trip_history=[_wrap_as_trip(_trip_dict("brand-new"))],
    )
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED),
    )
    v.get_recent_trips.assert_called_once()
    assert v._captured["limit"] == 2  # delta-fetch
    cached = mgr.cache.get("VIN1")
    assert cached[0]["id"] == "brand-new"
    assert cached[1]["id"] == "cached"


@pytest.mark.asyncio
async def test_piggyback_cold_start_when_history_has_trips(hass):
    """Empty cache + trip_history non-empty → cold-start fires."""
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    v = _make_vehicle([_trip_dict(f"t{i}") for i in range(3)])
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.NONE),
    )
    v.get_recent_trips.assert_called_once()
    assert v._captured["limit"] == 5  # cold-start uses max
    assert v._captured["with_route"] is True


@pytest.mark.asyncio
async def test_piggyback_uuid_id_compares_as_string(hass):
    """Pytoyoda Trip._trip.id is a UUID; cache stores str. Compare must align."""
    from uuid import UUID

    trip_uuid = UUID("49743b6d-3078-4efe-a68f-6c826b2680b6")
    cached_dict = {**_trip_dict("ignored"), "id": str(trip_uuid)}
    mgr = RecentTripsManager(hass, _make_entry(), max_recent_trips=5)
    await mgr.async_setup()
    mgr.cache.set("VIN1", [cached_dict])

    # Simulate pytoyoda: history[0]._trip.id is the UUID object itself.
    inner = MagicMock()
    inner.id = trip_uuid
    history_wrapper = SimpleNamespace(_trip=inner)
    v = SimpleNamespace(
        alias="RAV4",
        get_recent_trips=AsyncMock(),
        trip_history=[history_wrapper],
    )
    await mgr.async_maybe_refresh(
        v, "VIN1", _make_decision(RefreshTrigger.JUST_STOPPED),
    )
    # UUID stringified == cache id string → match → no fetch.
    v.get_recent_trips.assert_not_called()
    assert mgr._followup_pending.get("VIN1") is True
