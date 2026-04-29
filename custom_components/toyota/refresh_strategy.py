"""Smart status-refresh decision tree (per-VIN, per coordinator cycle).

Pure function module: takes a state snapshot, returns a RefreshDecision
describing what the coordinator should do this cycle for one VIN. No
hass / no I/O / no time.now() - all "now" values are passed in. This makes
the decision tree deterministically unit-testable without booting hass.

Spec source: rate-limit-remediation-plan.md Addendum 4 (2026-04-25).
Background: we discovered on 2026-04-24 that Toyota's `/v1/global/remote/status`
returns 429+APIGW-403 ("Unauthorized") when its server-side cache is empty
or stale, NOT because we're hitting a rate limit. The cache is populated
by either auto-reports from a transmitting (driving) car, or by an explicit
POST `/refresh-status` (a "wake" request). The Toyota Android app issues
the POST before reading status. This module decides when WE should issue
that POST, balancing fresh data against 12V-battery / cellular costs.

Caller contract (the coordinator's `_refresh_one_vehicle`):

After enacting a RefreshDecision, the caller MUST update VinState as
follows. The decision tree itself is side-effect-free; mutation happens
in the caller so retain-vs-propagate failure semantics stay there.

  - Always at end of cycle:
      state.last_odometer_km = snapshot.current_odometer_km
      state.was_moving_last_cycle = (movement detected this cycle)

  - When action == POST_THEN_GET:
      state.last_post_attempt_at = now
      if decision.trigger is JUST_STOPPED:
          state.remaining_post_cycles = options.post_count_per_stop - 1
      elif decision.trigger is JUST_STOPPED_FOLLOWUP:
          state.remaining_post_cycles = max(0, state.remaining_post_cycles - 1)
      # SERVICE_CALL / IDLE_WAKE do not touch remaining_post_cycles.
      On Layer 1 result: call on_post_layer1_failure() or
        on_post_layer1_success().
      On poll-loop outcome:
        - occurrence advanced: call on_occurrence_advanced(state, new_occ)
        - timed out: call on_wake_failed(state, options)

  - When action == GET_ONLY:
      On 200: state.last_status_fetch_at = now
              if occurrence advanced: call on_occurrence_advanced(...)
      On 429: leave state alone.

  - When action == SERVE_FROM_CACHE:
      No state mutation needed. Caller serves cached LockStatus.

  - When action == HARD_DISABLED:
      Caller falls through to legacy path (bare vehicle.update()).
      No new state mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Number of consecutive Layer 1 (gateway-rejected) wake POSTs that auto-disables
# refresh-status for the entire config entry. Per remediation-plan Addendum 4.
_AUTO_DISABLE_REJECTION_THRESHOLD = 2


class RefreshAction(StrEnum):
    """What this cycle should do for one VIN."""

    POST_THEN_GET = "post_then_get"
    """Issue POST /refresh-status, then poll /status until occurrence_date
    advances or the in-cycle timeout expires."""

    GET_ONLY = "get_only"
    """Issue GET /status only (cache may already be populated by an external
    source or a moving car's auto-reports)."""

    SERVE_FROM_CACHE = "serve_from_cache"
    """Skip both the POST and the GET; serve LockStatus from last_good_response.
    Other endpoints already refreshed elsewhere this cycle."""

    HARD_DISABLED = "hard_disabled"
    """User toggle off, OR auto-disabled because the vehicle doesn't support
    refresh-status. Coordinator should fall through to legacy refresh path
    (single vehicle.update() call, serve cached LockStatus on stale)."""


class RefreshState(StrEnum):
    """Steady-state value of the diagnostic sensor `<alias>_status_refresh_state`.

    These are user-facing translation keys; do not rename without updating
    translations/en.json + every other locale.
    """

    ACTIVE = "active"
    SOFT_DISABLED_UNREACHABLE = "soft_disabled_unreachable"
    HARD_DISABLED_AUTO = "hard_disabled_auto"
    HARD_DISABLED_USER = "hard_disabled_user"


class RefreshTrigger(StrEnum):
    """Why the strategy chose to act (debug logging + diagnostic sensor)."""

    NONE = "none"
    SERVICE_CALL = "service_call"
    JUST_STOPPED = "just_stopped"
    JUST_STOPPED_FOLLOWUP = "just_stopped_followup"
    CURRENTLY_MOVING = "currently_moving"
    IDLE_WAKE = "idle_wake"
    CACHE_STALE = "cache_stale"
    CACHE_EMPTY = "cache_empty"


# ----------------------------------------------------------------------------
# Inputs (snapshot of state at the start of one VIN's cycle).
# ----------------------------------------------------------------------------


@dataclass
class StrategyOptions:
    """Mirror of the user-configurable options from config_entry.options."""

    enable_status_refresh: bool = True
    auto_disabled_status_refresh: bool = False
    # Hours between idle-wake POSTs. 0 disables the feature; >0 fires a wake
    # POST every N hours when the car has not moved.
    idle_wake_hours: int = 0
    failed_wake_threshold: int = 3
    max_cache_age_minutes: int = 30
    # How many wake POSTs to fire per stop event. The first POST goes out the
    # cycle just_stopped is detected; the remaining N-1 POSTs go out on the
    # immediately following cycles. Cycle-count based (not wall-clock) so that
    # the behaviour is consistent regardless of polling interval.
    post_count_per_stop: int = 2


@dataclass
class VinState:
    """Per-VIN runtime state. Stored in hass.data[DOMAIN][f"{entry_id}_diag"]."""

    # Movement / odometer tracking. None on first cycle ever for this VIN.
    last_odometer_km: float | None = None
    was_moving_last_cycle: bool = False

    # Cache freshness (server-side, mirrored on our end).
    last_status_occurrence_date: datetime | None = None
    last_status_fetch_at: datetime | None = None  # OUR last GET that returned 200

    # Wake bookkeeping.
    last_post_attempt_at: datetime | None = None
    consecutive_failed_wakes: int = 0
    consecutive_post_rejections: int = 0
    soft_disabled: bool = False
    # Remaining POST cycles for the in-progress stop event. Set to
    # post_count_per_stop - 1 by the caller when JUST_STOPPED fires; decremented
    # by the caller after each followup POST. > 0 -> next cycle fires another
    # POST trigger=just_stopped_followup. Clears naturally to 0.
    remaining_post_cycles: int = 0

    # Whether last_good_response is non-None - used to short-circuit
    # SERVE_FROM_CACHE when there's nothing to serve.
    has_cached_response: bool = False


@dataclass
class CycleSnapshot:
    """Everything the strategy needs to decide one cycle for one VIN."""

    now: datetime
    current_odometer_km: float | None  # from telemetry (this cycle, post-update)
    state: VinState
    options: StrategyOptions
    user_service_call_pending: bool = False


# ----------------------------------------------------------------------------
# Outputs.
# ----------------------------------------------------------------------------


@dataclass
class RefreshDecision:
    """The strategy's verdict for one cycle."""

    action: RefreshAction
    trigger: RefreshTrigger
    refresh_state: RefreshState


# ----------------------------------------------------------------------------
# The decision tree. ~50 lines of straight-line logic. Side-effect-free; the
# caller is responsible for applying mutations to VinState based on what
# actually happens after the decision is enacted.
# ----------------------------------------------------------------------------


def _hard_disable_decision(
    opts: StrategyOptions,
    *,
    user_service_call_pending: bool = False,
) -> RefreshDecision | None:
    """Return a HARD_DISABLED decision if either disable flag is set, else None.

    Service calls bypass BOTH disable forms. The convention everywhere in
    HA is "polling toggle stops automatic polling, manual service calls
    still work" - so enable_status_refresh:False means "stop the strategy's
    cadence" rather than "lock out POSTs entirely". Users who want a
    bespoke schedule (geofence arrival, garage-door close, etc.) disable
    the cadence and drive POSTs from their own automations against the
    refresh_vehicle_status service. After a successful service-call POST,
    auto_disabled_status_refresh is cleared by the integration so a future
    cadence re-enable doesn't land in the auto-disabled state.
    """
    if not opts.enable_status_refresh and not user_service_call_pending:
        return RefreshDecision(
            action=RefreshAction.HARD_DISABLED,
            trigger=RefreshTrigger.NONE,
            refresh_state=RefreshState.HARD_DISABLED_USER,
        )
    if opts.auto_disabled_status_refresh and not user_service_call_pending:
        return RefreshDecision(
            action=RefreshAction.HARD_DISABLED,
            trigger=RefreshTrigger.NONE,
            refresh_state=RefreshState.HARD_DISABLED_AUTO,
        )
    return None


def _resolve_post_trigger(
    snapshot: CycleSnapshot,
    *,
    car_just_stopped: bool,
    car_currently_moving: bool,
) -> tuple[bool, RefreshTrigger]:
    """Pick whether this cycle should POST and the diagnostic trigger label.

    Order of precedence is significant: explicit user requests beat in-flight
    stop followups beat fresh stop detection beat idle-wake. Movement is the
    fallback "informational" trigger when none of the above fire.
    """
    state = snapshot.state
    opts = snapshot.options
    now = snapshot.now

    if snapshot.user_service_call_pending:
        return True, RefreshTrigger.SERVICE_CALL
    if state.remaining_post_cycles > 0:
        return True, RefreshTrigger.JUST_STOPPED_FOLLOWUP
    if car_just_stopped:
        return True, RefreshTrigger.JUST_STOPPED
    if car_currently_moving:
        return False, RefreshTrigger.CURRENTLY_MOVING
    if opts.idle_wake_hours > 0 and (
        state.last_post_attempt_at is None
        or (now - state.last_post_attempt_at) >= timedelta(hours=opts.idle_wake_hours)
    ):
        return True, RefreshTrigger.IDLE_WAKE
    return False, RefreshTrigger.NONE


def decide(snapshot: CycleSnapshot) -> RefreshDecision:
    """Pure decision: what should this cycle do for this VIN?

    Caller (the coordinator) is responsible for executing the action and
    updating state based on outcomes.
    """
    opts = snapshot.options
    state = snapshot.state
    now = snapshot.now

    hard = _hard_disable_decision(
        opts, user_service_call_pending=snapshot.user_service_call_pending
    )
    if hard is not None:
        return hard

    # Movement state from telemetry (already fetched by the caller's
    # vehicle.update() this cycle).
    car_currently_moving = (
        state.last_odometer_km is not None
        and snapshot.current_odometer_km is not None
        and snapshot.current_odometer_km != state.last_odometer_km
    )
    car_just_stopped = state.was_moving_last_cycle and not car_currently_moving

    should_post, trigger = _resolve_post_trigger(
        snapshot,
        car_just_stopped=car_just_stopped,
        car_currently_moving=car_currently_moving,
    )

    # Soft-disable suppresses POST (still allows GET so we can detect external
    # cache repopulation) but keeps the trigger label for diagnostics.
    soft_disabled = state.soft_disabled
    if soft_disabled:
        should_post = False

    # Decide if a GET is needed even when no POST.
    cache_age = (
        (now - state.last_status_fetch_at)
        if state.last_status_fetch_at
        else timedelta(days=365)
    )
    cache_stale = cache_age > timedelta(minutes=opts.max_cache_age_minutes)
    cache_empty = state.last_status_occurrence_date is None

    # NB: car_currently_moving is intentionally NOT in this OR-chain. During a
    # drive we can serve the pre-drive lock state from cache; refreshing only
    # when actually stale avoids ~40 useless /status calls on a long trip.
    should_get = car_just_stopped or should_post or cache_stale or cache_empty

    refresh_state = (
        RefreshState.SOFT_DISABLED_UNREACHABLE if soft_disabled else RefreshState.ACTIVE
    )

    if should_post:
        return RefreshDecision(
            action=RefreshAction.POST_THEN_GET,
            trigger=trigger,
            refresh_state=refresh_state,
        )

    if should_get:
        if trigger is RefreshTrigger.NONE:
            if cache_empty:
                trigger = RefreshTrigger.CACHE_EMPTY
            elif cache_stale:
                trigger = RefreshTrigger.CACHE_STALE
        return RefreshDecision(
            action=RefreshAction.GET_ONLY,
            trigger=trigger,
            refresh_state=refresh_state,
        )

    return RefreshDecision(
        action=RefreshAction.SERVE_FROM_CACHE,
        trigger=trigger,
        refresh_state=refresh_state,
    )


# ----------------------------------------------------------------------------
# State-mutation helpers. Caller invokes these after enacting a decision and
# observing the outcome. Keeping them in this module so the strategy and its
# state-machine bookkeeping live together.
# ----------------------------------------------------------------------------


def on_post_layer1_failure(state: VinState, _options: StrategyOptions) -> bool:
    """Record a non-000000 returnCode from POST.

    Returns True if this should trigger auto-disable (hard-disable) at the
    config-entry level. Per Addendum 4 step 11b: 2 consecutive rejections ->
    auto-disable. The options arg is reserved for a future user-tunable
    threshold; currently unused.
    """
    state.consecutive_post_rejections += 1
    return state.consecutive_post_rejections >= _AUTO_DISABLE_REJECTION_THRESHOLD


def on_post_layer1_success(state: VinState) -> None:
    """Reset the rejection counter; POST was accepted by the gateway."""
    state.consecutive_post_rejections = 0


def on_wake_failed(state: VinState, options: StrategyOptions) -> None:
    """Record that POST polling expired without occurrence_date advancement.

    Per Addendum 4 step 11c: at threshold, soft-disable this VIN.
    Caller should clear just_stopped_followup_due_at; that's separate state.
    """
    state.consecutive_failed_wakes += 1
    if state.consecutive_failed_wakes >= options.failed_wake_threshold:
        state.soft_disabled = True


def on_occurrence_advanced(state: VinState, new_occurrence: datetime) -> None:
    """Cache successfully advanced.

    Either POST polling succeeded, OR an external source warmed the cache.
    Clears soft-disable per Addendum 4 step 14.
    """
    state.last_status_occurrence_date = new_occurrence
    state.consecutive_failed_wakes = 0
    state.soft_disabled = False
