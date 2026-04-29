"""Unit tests for the smart-refresh decision tree.

The decision tree is a pure function so these tests don't need hass or
network. Each test sets up a CycleSnapshot, calls decide(), and asserts the
RefreshDecision is what Addendum 4 prescribes.

Numbering refers to the steps in `rate-limit-remediation-plan.md` Addendum 4.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.toyota.refresh_strategy import (
    CycleSnapshot,
    RefreshAction,
    RefreshState,
    RefreshTrigger,
    StrategyOptions,
    VinState,
    decide,
    on_occurrence_advanced,
    on_post_layer1_failure,
    on_post_layer1_success,
    on_wake_failed,
)


NOW = datetime(2026, 4, 25, 10, 0, 0, tzinfo=timezone.utc)


def _snap(**overrides) -> CycleSnapshot:
    """Build a CycleSnapshot with safe defaults; overrides patch any field."""
    state = overrides.pop("state", VinState())
    options = overrides.pop("options", StrategyOptions())
    return CycleSnapshot(
        now=overrides.pop("now", NOW),
        current_odometer_km=overrides.pop("current_odometer_km", 1000.0),
        state=state,
        options=options,
        user_service_call_pending=overrides.pop("user_service_call_pending", False),
    )


# ---------------------------------------------------------------------------
# Step 1: hard-disable paths
# ---------------------------------------------------------------------------


def test_user_disabled_returns_hard_disabled_user():
    s = _snap(options=StrategyOptions(enable_status_refresh=False))
    d = decide(s)
    assert d.action is RefreshAction.HARD_DISABLED
    assert d.refresh_state is RefreshState.HARD_DISABLED_USER


def test_auto_disabled_returns_hard_disabled_auto():
    s = _snap(
        options=StrategyOptions(
            enable_status_refresh=True, auto_disabled_status_refresh=True
        )
    )
    d = decide(s)
    assert d.action is RefreshAction.HARD_DISABLED
    assert d.refresh_state is RefreshState.HARD_DISABLED_AUTO


def test_service_call_bypasses_hard_disabled_auto():
    """Service call overrides auto-disable so the user can retry manually."""
    s = _snap(
        options=StrategyOptions(
            enable_status_refresh=True, auto_disabled_status_refresh=True
        ),
        user_service_call_pending=True,
    )
    d = decide(s)
    assert d.action is RefreshAction.POST_THEN_GET
    assert d.trigger is RefreshTrigger.SERVICE_CALL


def test_service_call_bypasses_hard_disabled_user():
    """Service call also bypasses user-disable, matching HA convention.

    `enable_status_refresh: False` stops the automatic cadence; explicit
    service-call invocations (e.g. a garage-door automation calling
    `refresh_vehicle_status`) still go through. Users who want full lockout
    simply do not invoke the service.
    """
    s = _snap(
        options=StrategyOptions(
            enable_status_refresh=False, auto_disabled_status_refresh=False
        ),
        user_service_call_pending=True,
    )
    d = decide(s)
    assert d.action is RefreshAction.POST_THEN_GET
    assert d.trigger is RefreshTrigger.SERVICE_CALL


def test_user_disable_blocks_non_service_triggers():
    """Without a service call, user-disable still blocks the strategy."""
    s = _snap(
        options=StrategyOptions(
            enable_status_refresh=False, auto_disabled_status_refresh=False
        ),
        user_service_call_pending=False,
    )
    d = decide(s)
    assert d.action is RefreshAction.HARD_DISABLED
    assert d.refresh_state is RefreshState.HARD_DISABLED_USER


# ---------------------------------------------------------------------------
# Step 4: service-call wins over everything except hard-disable
# ---------------------------------------------------------------------------


def test_service_call_triggers_post(  # noqa: D103
):
    s = _snap(user_service_call_pending=True)
    d = decide(s)
    assert d.action is RefreshAction.POST_THEN_GET
    assert d.trigger is RefreshTrigger.SERVICE_CALL


# ---------------------------------------------------------------------------
# just_stopped triggers POST; remaining_post_cycles drives the followup count
# ---------------------------------------------------------------------------


def test_just_stopped_triggers_post():
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=True,
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.POST_THEN_GET
    assert d.trigger is RefreshTrigger.JUST_STOPPED


def test_remaining_post_cycles_triggers_followup_post():
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=False,
        remaining_post_cycles=1,
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.POST_THEN_GET
    assert d.trigger is RefreshTrigger.JUST_STOPPED_FOLLOWUP


def test_remaining_post_cycles_zero_means_no_followup():
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=False,
        remaining_post_cycles=0,
        last_status_occurrence_date=NOW,
        last_status_fetch_at=NOW,
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.SERVE_FROM_CACHE


# ---------------------------------------------------------------------------
# currently_moving: cache fresh -> SERVE_FROM_CACHE (no /status calls during
# a long drive). Cache stale -> GET_ONLY (refresh).
# ---------------------------------------------------------------------------


def test_currently_moving_with_fresh_cache_serves_from_cache():
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=True,
        last_status_occurrence_date=NOW - timedelta(minutes=2),
        last_status_fetch_at=NOW - timedelta(minutes=2),
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=1010.0)
    d = decide(s)
    assert d.action is RefreshAction.SERVE_FROM_CACHE
    assert d.trigger is RefreshTrigger.CURRENTLY_MOVING


def test_currently_moving_with_stale_cache_does_get():
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=True,
        last_status_occurrence_date=NOW - timedelta(hours=1),
        last_status_fetch_at=NOW - timedelta(hours=1),
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=1010.0)
    d = decide(s)
    assert d.action is RefreshAction.GET_ONLY
    # Trigger may be CURRENTLY_MOVING (set first) or CACHE_STALE (set as
    # diagnostic fallback when no other trigger fired). Accept either.
    assert d.trigger in (
        RefreshTrigger.CURRENTLY_MOVING,
        RefreshTrigger.CACHE_STALE,
    )


# ---------------------------------------------------------------------------
# Step 7: idle wake (opt-in)
# ---------------------------------------------------------------------------


def test_idle_wake_zero_hours_means_no_post_when_idle():
    """idle_wake_hours=0 (default) disables the feature; idle cars don't POST."""
    state = VinState(
        last_odometer_km=1000.0,
        last_status_occurrence_date=NOW - timedelta(minutes=5),
        last_status_fetch_at=NOW - timedelta(minutes=5),
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.SERVE_FROM_CACHE


def test_idle_wake_triggers_post_after_interval():
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=False,
        last_post_attempt_at=NOW - timedelta(hours=13),
        last_status_occurrence_date=NOW,
        last_status_fetch_at=NOW,
        has_cached_response=True,
    )
    options = StrategyOptions(idle_wake_hours=12)
    s = _snap(state=state, options=options, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.POST_THEN_GET
    assert d.trigger is RefreshTrigger.IDLE_WAKE


def test_idle_wake_within_interval_skips_post():
    state = VinState(
        last_odometer_km=1000.0,
        last_post_attempt_at=NOW - timedelta(hours=2),
        last_status_occurrence_date=NOW,
        last_status_fetch_at=NOW,
        has_cached_response=True,
    )
    options = StrategyOptions(idle_wake_hours=12)
    s = _snap(state=state, options=options, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.SERVE_FROM_CACHE


# ---------------------------------------------------------------------------
# Step 9: cache stale / empty triggers GET_ONLY
# ---------------------------------------------------------------------------


def test_cache_empty_triggers_get():
    state = VinState(last_odometer_km=1000.0, has_cached_response=False)
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.GET_ONLY
    assert d.trigger is RefreshTrigger.CACHE_EMPTY


def test_cache_stale_triggers_get():
    state = VinState(
        last_odometer_km=1000.0,
        last_status_occurrence_date=NOW - timedelta(minutes=45),
        last_status_fetch_at=NOW - timedelta(minutes=45),
        has_cached_response=True,
    )
    options = StrategyOptions(max_cache_age_minutes=30)
    s = _snap(state=state, options=options, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.GET_ONLY
    assert d.trigger is RefreshTrigger.CACHE_STALE


# ---------------------------------------------------------------------------
# Step 10: soft-disable suppresses POST but allows GET
# ---------------------------------------------------------------------------


def test_soft_disable_suppresses_post_but_allows_get_when_stale():
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=True,
        soft_disabled=True,
        has_cached_response=True,
    )
    # Just-stopped would normally POST; soft-disable downgrades to GET only
    # because should_get_status remains True (car_just_stopped path).
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.GET_ONLY
    assert d.refresh_state is RefreshState.SOFT_DISABLED_UNREACHABLE


# ---------------------------------------------------------------------------
# Step 13: serve from cache when nothing fired
# ---------------------------------------------------------------------------


def test_idle_fresh_cache_serves_from_cache():
    """Stationary car, fresh cache, no triggers fired -> skip both."""
    state = VinState(
        last_odometer_km=1000.0,
        was_moving_last_cycle=False,
        last_status_occurrence_date=NOW - timedelta(minutes=2),
        last_status_fetch_at=NOW - timedelta(minutes=2),
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    assert d.action is RefreshAction.SERVE_FROM_CACHE


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def test_layer1_failure_counter_triggers_auto_disable_after_two():
    state = VinState()
    options = StrategyOptions()
    assert on_post_layer1_failure(state, options) is False
    assert state.consecutive_post_rejections == 1
    assert on_post_layer1_failure(state, options) is True  # auto-disable now
    assert state.consecutive_post_rejections == 2


def test_layer1_success_resets_rejection_counter():
    state = VinState(consecutive_post_rejections=1)
    on_post_layer1_success(state)
    assert state.consecutive_post_rejections == 0


def test_wake_failed_triggers_soft_disable_at_threshold():
    state = VinState()
    options = StrategyOptions(failed_wake_threshold=3)
    on_wake_failed(state, options)
    on_wake_failed(state, options)
    assert state.soft_disabled is False
    on_wake_failed(state, options)
    assert state.soft_disabled is True
    assert state.consecutive_failed_wakes == 3


def test_occurrence_advanced_clears_soft_disable():
    state = VinState(
        soft_disabled=True,
        consecutive_failed_wakes=5,
        last_status_occurrence_date=NOW - timedelta(hours=1),
    )
    on_occurrence_advanced(state, NOW)
    assert state.soft_disabled is False
    assert state.consecutive_failed_wakes == 0
    assert state.last_status_occurrence_date == NOW


# ---------------------------------------------------------------------------
# Edge: first cycle ever (no last_odometer_km baseline yet)
# ---------------------------------------------------------------------------


def test_first_cycle_no_movement_inferred():
    """First cycle: state.last_odometer_km is None. No movement signal possible.
    Strategy should NOT classify this as "just stopped" (would falsely POST)."""
    state = VinState(last_odometer_km=None, was_moving_last_cycle=False)
    s = _snap(state=state, current_odometer_km=1000.0)
    d = decide(s)
    # cache_empty is True (last_status_occurrence_date is None default), so GET
    assert d.action is RefreshAction.GET_ONLY
    assert d.trigger in (RefreshTrigger.CACHE_EMPTY, RefreshTrigger.CACHE_STALE)


def test_no_telemetry_this_cycle_does_not_crash():
    """Telemetry might be unavailable; current_odometer_km can be None.
    Strategy must not break."""
    state = VinState(
        last_odometer_km=1000.0,
        last_status_occurrence_date=NOW,
        last_status_fetch_at=NOW,
        has_cached_response=True,
    )
    s = _snap(state=state, current_odometer_km=None)
    d = decide(s)
    # No movement detected (current_odometer_km is None -> not moving).
    # Cache fresh, no other triggers -> serve from cache.
    assert d.action is RefreshAction.SERVE_FROM_CACHE
