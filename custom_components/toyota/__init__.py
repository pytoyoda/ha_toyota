"""Toyota EU community integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

import asyncio
import asyncio.exceptions as asyncioexceptions
import contextlib
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, TypedDict, TypeVar

import httpcore
import httpx
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from loguru import logger
from pydantic import ValidationError

from .const import (
    CONF_AUTO_DISABLED_STATUS_REFRESH,
    CONF_BRAND,
    CONF_ENABLE_STATUS_REFRESH,
    CONF_FAILED_WAKE_THRESHOLD,
    CONF_IDLE_WAKE_HOURS,
    CONF_MAX_CACHE_AGE_MINUTES,
    CONF_METRIC_VALUES,
    CONF_POLLING_INTERVAL_MINUTES,
    CONF_POST_COUNT_PER_STOP,
    CONF_RETAIN_ON_TRANSIENT_FAILURE,
    DEFAULT_AUTO_DISABLED_STATUS_REFRESH,
    DEFAULT_ENABLE_STATUS_REFRESH,
    DEFAULT_FAILED_WAKE_THRESHOLD,
    DEFAULT_IDLE_WAKE_HOURS,
    DEFAULT_MAX_CACHE_AGE_MINUTES,
    DEFAULT_POLLING_INTERVAL_MINUTES,
    DEFAULT_POST_COUNT_PER_STOP,
    DEFAULT_RETAIN_ON_TRANSIENT_FAILURE,
    DOMAIN,
    PLATFORMS,
    STARTUP_MESSAGE,
)
from .refresh_strategy import (
    CycleSnapshot,
    RefreshAction,
    RefreshDecision,
    RefreshTrigger,
    StrategyOptions,
    VinState,
    decide,
    on_occurrence_advanced,
    on_post_layer1_failure,
    on_post_layer1_success,
    on_wake_failed,
)

_LOGGER = logging.getLogger(__name__)

# Default wake-poll budget in seconds. Used when POST_THEN_GET fires from a
# non-service trigger (just_stopped, just_stopped_followup, idle_wake) -
# i.e. the budget the strategy itself owns. Service-call triggers carry
# their own user-supplied timeout via pending_service_calls and override
# this default. Empirically the modem wakes within ~10-25s after a POST,
# so 25s is enough for ~3 polls at 10s spacing without burning extra
# requests against the gateway.
STRATEGY_DEFAULT_WAKE_TIMEOUT_S = 25


def loguru_to_hass(message: str) -> None:
    """Forward Loguru logs to standard Python logger used by HACS."""
    level_name = message.record["level"].name.lower()

    if "debug" in level_name:
        _LOGGER.debug(message)
    elif "info" in level_name:
        _LOGGER.info(message)
    elif "warn" in level_name:
        _LOGGER.warning(message)
    elif "error" in level_name:
        _LOGGER.error(message)
    else:
        _LOGGER.critical(message)


logger.remove()
logger.configure(handlers=[{"sink": loguru_to_hass}])

# These imports must be after Loguru configuration to properly intercept logging
from pytoyoda.client import MyT  # noqa: E402
from pytoyoda.exceptions import (  # noqa: E402
    ToyotaApiError,
    ToyotaInternalError,
    ToyotaLoginError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall
    from pytoyoda.models.summary import Summary
    from pytoyoda.models.vehicle import Vehicle

_T = TypeVar("_T")


class StatisticsData(TypedDict):
    """Representing Statistics data."""

    day: Summary | None
    week: Summary | None
    month: Summary | None
    year: Summary | None


class VehicleData(TypedDict):
    """Representing Vehicle data."""

    data: Vehicle
    statistics: StatisticsData | None
    metric_values: bool
    # Observability fields, populated by the coordinator regardless of the
    # CONF_RETAIN_ON_TRANSIENT_FAILURE toggle. Surfaced as timestamp +
    # diagnostic sensors so users can see when their car data was last fresh
    # and what the most recent Toyota-side hiccup was.
    last_successful_fetch: datetime | None
    last_error_time: datetime | None
    last_error_code: str | None
    # True when this poll's data is a cached fallback because the live fetch
    # failed. Used by downstream sensors as a diagnostic.
    is_cached: bool


async def async_setup_entry(  # pylint: disable=too-many-statements # noqa: PLR0915, C901
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Toyota Connected Services from a config entry."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]
    metric_values = entry.data[CONF_METRIC_VALUES]
    brand = entry.data.get(
        CONF_BRAND, "toyota"
    )  # Get brand from config, default to toyota

    # Map brand selection to API brand code
    brand_map = {"toyota": "T", "lexus": "L"}
    brand_code = brand_map.get(brand, "T")

    _LOGGER.info("Setting up %s integration (brand code: %s)", brand, brand_code)

    client = MyT(
        username=email,
        password=password,
        use_metric=metric_values,
        brand=brand_code,
    )

    try:
        await client.login()
    except ToyotaLoginError as ex:
        raise ConfigEntryAuthFailed(ex) from ex
    except (httpx.ConnectTimeout, httpcore.ConnectTimeout) as ex:
        msg = "Unable to connect to Toyota Connected Services"
        raise ConfigEntryNotReady(msg) from ex

    # Per-vehicle retain state. Keyed by VIN. The latest successful
    # VehicleData for each car is kept so that when ONE car's refresh
    # fails (e.g. Toyota 429s partway through the fleet sweep) we can
    # keep that car visible with its last fresh data + a diagnostic
    # last_error timestamp/code, while the other cars get fresh data.
    # Without this, any single transient Toyota failure flips the
    # entire fleet to unavailable.
    #
    # Three additional pieces of state are surfaced as HA sensors:
    # - last_successful_fetch: when this car's data was last fresh
    # - last_error_time: when this car last hit a Toyota-side error
    # - last_error_code: the HTTP status or exception class of that error
    #
    # Gated behind CONF_RETAIN_ON_TRANSIENT_FAILURE so users who rely
    # on "unavailable" state transitions in their automations can opt
    # out. Default off for backward compatibility.
    retain_on_transient: bool = entry.options.get(
        CONF_RETAIN_ON_TRANSIENT_FAILURE, DEFAULT_RETAIN_ON_TRANSIENT_FAILURE
    )
    # Smart-refresh strategy options. See refresh_strategy.py for the design.
    enable_status_refresh: bool = entry.options.get(
        CONF_ENABLE_STATUS_REFRESH, DEFAULT_ENABLE_STATUS_REFRESH
    )
    auto_disabled_status_refresh: bool = entry.options.get(
        CONF_AUTO_DISABLED_STATUS_REFRESH, DEFAULT_AUTO_DISABLED_STATUS_REFRESH
    )
    idle_wake_hours: int = entry.options.get(
        CONF_IDLE_WAKE_HOURS, DEFAULT_IDLE_WAKE_HOURS
    )
    failed_wake_threshold: int = entry.options.get(
        CONF_FAILED_WAKE_THRESHOLD, DEFAULT_FAILED_WAKE_THRESHOLD
    )
    max_cache_age_minutes: int = entry.options.get(
        CONF_MAX_CACHE_AGE_MINUTES, DEFAULT_MAX_CACHE_AGE_MINUTES
    )
    polling_interval_minutes: int = entry.options.get(
        CONF_POLLING_INTERVAL_MINUTES, DEFAULT_POLLING_INTERVAL_MINUTES
    )
    post_count_per_stop: int = entry.options.get(
        CONF_POST_COUNT_PER_STOP, DEFAULT_POST_COUNT_PER_STOP
    )
    # Persist per-VIN state in hass.data so it survives config entry reload
    # (options flow triggers a reload, which would otherwise recreate these as
    # empty and wipe both the retain cache and the diag sensor history). Scoped
    # per entry_id so multiple Toyota accounts don't collide. Also unblocks the
    # setup path when the account is actively rate-limited: a fresh reload with
    # retain=OFF and no cache would loop in setup_retry forever, but with cache
    # preserved the retain=ON stub path (or the retained fleet) gets us through
    # first_refresh even under 429 pressure.
    diag_bucket = hass.data[DOMAIN].setdefault(
        f"{entry.entry_id}_diag",
        {
            "last_good_per_vin": {},
            "last_fetch_time_per_vin": {},
            "last_error_per_vin": {},
        },
    )
    # Defensive setdefault for new keys: existing entries from Phase 1 have only
    # the original three. Each new key auto-created on first cycle for any
    # encountered VIN; declared here at bucket level for clarity.
    for new_key in (
        "last_status_occurrence_date_per_vin",
        "last_status_fetch_at_per_vin",
        "last_post_attempt_at_per_vin",
        "last_odometer_km_per_vin",
        "was_moving_last_cycle_per_vin",
        "consecutive_failed_wakes_per_vin",
        "consecutive_post_rejections_per_vin",
        "soft_disabled_per_vin",
        "remaining_post_cycles_per_vin",
        "last_status_refresh_state_per_vin",
        "last_status_refresh_trigger_per_vin",
        # Parsed RemoteStatusResponseModel from the most recent successful
        # /status fetch (GET_ONLY OR POST_THEN_GET). Re-injected into the
        # Vehicle's _endpoint_data on SERVE_FROM_CACHE cycles, since each
        # coordinator cycle gets a fresh Vehicle from get_vehicles() with
        # empty _endpoint_data. Without this, lock/door/window/hood sensors
        # flip back to "unknown" between Toyota fetches.
        "last_status_response_per_vin",
    ):
        diag_bucket.setdefault(new_key, {})
    # Pending service-call requests, keyed by VIN. The service handler sets a
    # value here and reload-triggers the coordinator; the next cycle picks it
    # up via the strategy's user_service_call_pending input. The dict value is
    # the user-supplied wake-poll budget in seconds (services.yaml exposes
    # `timeout_seconds`, default 60); non-service triggers (just_stopped,
    # idle_wake, ...) fall through to STRATEGY_DEFAULT_WAKE_TIMEOUT_S below.
    pending_service_calls: dict[str, int] = diag_bucket.setdefault(
        "pending_service_calls", {}
    )
    last_good_per_vin: dict[str, VehicleData] = diag_bucket["last_good_per_vin"]
    last_fetch_time_per_vin: dict[str, datetime] = diag_bucket[
        "last_fetch_time_per_vin"
    ]
    last_error_per_vin: dict[str, tuple[datetime, str]] = diag_bucket[
        "last_error_per_vin"
    ]

    exception_code_map: list[tuple[tuple[type[BaseException], ...], str]] = [
        ((httpx.ConnectTimeout, httpcore.ConnectTimeout), "connect timeout"),
        ((httpx.ReadTimeout, asyncioexceptions.TimeoutError), "read timeout"),
        ((asyncioexceptions.CancelledError,), "cancelled"),
        ((ToyotaApiError,), "api error"),
        ((ToyotaLoginError,), "login error"),
    ]

    def _error_code(exc: BaseException) -> str:
        """Derive a short error-code string for the last_error sensor."""
        msg = str(exc)
        # Toyota 429s embed the status code in the ToyotaApiError message:
        # "Request Failed. 429, {...}." Extract it when present.
        for code in ("429", "500", "502", "503", "504"):
            if f"Request Failed. {code}," in msg:
                return f"HTTP {code}"
        for exc_types, label in exception_code_map:
            if isinstance(exc, exc_types):
                return label
        return type(exc).__name__

    def _build_vehicle_data_from_cache(vin: str) -> VehicleData:
        """Return a copy of the last-good VehicleData for a vin.

        Refreshes error/timestamp fields. The Vehicle object itself is the
        cached one, so all downstream sensors see the last good values.
        """
        cached = last_good_per_vin[vin]
        err = last_error_per_vin.get(vin)
        return VehicleData(
            data=cached["data"],
            statistics=cached["statistics"],
            metric_values=cached["metric_values"],
            last_successful_fetch=last_fetch_time_per_vin.get(vin),
            last_error_time=err[0] if err else None,
            last_error_code=err[1] if err else None,
            is_cached=True,
        )

    async def _call_tagged(
        endpoint_name: str, vin: str | None, coro: Awaitable[_T]
    ) -> _T:
        """Await a pytoyoda call, tagging any exception with the endpoint name.

        Lets us see per-endpoint 429 distribution in the HA log, e.g.
        ``Toyota 429 on week_summary for vin=...012600``. Needed to interpret
        the inter-call spacing sweep - if one endpoint 429s disproportionately,
        spacing alone won't fix it and we pivot.
        """
        try:
            return await coro
        except BaseException as ex:
            code = _error_code(ex)
            vin_tail = f"...{vin[-6:]}" if vin else "<no-vin>"
            _LOGGER.warning("Toyota %s on %s for vin=%s", code, endpoint_name, vin_tail)
            raise

    def _build_vin_state(vin: str) -> VinState:
        """Read the per-VIN diag dicts into a VinState snapshot for decide()."""
        return VinState(
            last_odometer_km=diag_bucket["last_odometer_km_per_vin"].get(vin),
            was_moving_last_cycle=diag_bucket["was_moving_last_cycle_per_vin"].get(
                vin, False
            ),
            last_status_occurrence_date=diag_bucket[
                "last_status_occurrence_date_per_vin"
            ].get(vin),
            last_status_fetch_at=diag_bucket["last_status_fetch_at_per_vin"].get(vin),
            last_post_attempt_at=diag_bucket["last_post_attempt_at_per_vin"].get(vin),
            consecutive_failed_wakes=diag_bucket[
                "consecutive_failed_wakes_per_vin"
            ].get(vin, 0),
            consecutive_post_rejections=diag_bucket[
                "consecutive_post_rejections_per_vin"
            ].get(vin, 0),
            soft_disabled=diag_bucket["soft_disabled_per_vin"].get(vin, False),
            remaining_post_cycles=diag_bucket["remaining_post_cycles_per_vin"].get(
                vin, 0
            ),
            has_cached_response=vin in last_good_per_vin,
        )

    def _persist_vin_state(vin: str, state: VinState) -> None:
        """Mirror VinState back into the diag bucket dicts."""
        diag_bucket["last_odometer_km_per_vin"][vin] = state.last_odometer_km
        diag_bucket["was_moving_last_cycle_per_vin"][vin] = state.was_moving_last_cycle
        diag_bucket["last_status_occurrence_date_per_vin"][vin] = (
            state.last_status_occurrence_date
        )
        diag_bucket["last_status_fetch_at_per_vin"][vin] = state.last_status_fetch_at
        diag_bucket["last_post_attempt_at_per_vin"][vin] = state.last_post_attempt_at
        diag_bucket["consecutive_failed_wakes_per_vin"][vin] = (
            state.consecutive_failed_wakes
        )
        diag_bucket["consecutive_post_rejections_per_vin"][vin] = (
            state.consecutive_post_rejections
        )
        diag_bucket["soft_disabled_per_vin"][vin] = state.soft_disabled
        diag_bucket["remaining_post_cycles_per_vin"][vin] = state.remaining_post_cycles

    def _strategy_options() -> StrategyOptions:
        return StrategyOptions(
            enable_status_refresh=enable_status_refresh,
            auto_disabled_status_refresh=auto_disabled_status_refresh,
            idle_wake_hours=idle_wake_hours,
            failed_wake_threshold=failed_wake_threshold,
            max_cache_age_minutes=max_cache_age_minutes,
            post_count_per_stop=post_count_per_stop,
        )

    async def _execute_post_then_get(
        vehicle: Vehicle,
        vin: str,
        state: VinState,
        timeout_s: int = STRATEGY_DEFAULT_WAKE_TIMEOUT_S,
    ) -> None:
        """Issue POST /refresh-status, then poll GET /status until cache advances.

        Polls until ``occurrence_date`` advances or ``timeout_s`` seconds expire.
        Defaults to STRATEGY_DEFAULT_WAKE_TIMEOUT_S for non-service triggers;
        service-call triggers pass through the user-supplied
        ``timeout_seconds`` from services.yaml. Mutates state in place per the
        caller contract in refresh_strategy.py. Pytoyoda's controller already
        retries 429/5xx with exponential backoff, so this loop only iterates
        if the gateway returned 200 with a stale occurrence_date (legitimate
        "POST accepted but cache not yet warm").

        On POST failure (exception OR non-"000000" returnCode): record a
        Layer 1 rejection, possibly auto-disable, then fall back to a bare
        GET so /status entities still refresh this cycle. See ha_toyota#293.
        On POST success: clear any prior auto-disable flag so a service-call
        retry (or a transient-5xx recovery) restores normal operation
        without requiring the user to toggle the option manually.
        """
        opts = _strategy_options()
        # POST raised after pytoyoda's retries exhausted (persistent gateway
        # 5xx) → post_response stays None and falls through to the Layer 1
        # failure branch below, same family as a non-"000000" returnCode
        # ("gateway will not process this POST"). _call_tagged has already
        # logged the underlying error.
        post_response = None
        with contextlib.suppress(
            ToyotaApiError,
            httpx.ConnectTimeout,
            httpcore.ConnectTimeout,
            asyncioexceptions.TimeoutError,
            httpx.ReadTimeout,
        ):
            post_response = await _call_tagged(
                "refresh_status", vin, vehicle.refresh_status()
            )
        state.last_post_attempt_at = dt_util.now()

        # Layer 1: gateway-level acceptance. payload.return_code "000000" =
        # accepted; anything else (or no response = exception path) =
        # vehicle does not support refresh-status this cycle.
        payload = getattr(post_response, "payload", None) if post_response else None
        return_code = getattr(payload, "return_code", None) if payload else None

        if return_code != "000000":
            should_auto_disable = on_post_layer1_failure(state, opts)
            if post_response is not None:
                # 200 OK with non-000000 returnCode (gateway-level rejection).
                _LOGGER.warning(
                    "Toyota refresh-status rejected for vin=...%s (returnCode=%s)",
                    vin[-6:],
                    return_code,
                )
            if should_auto_disable and not entry.options.get(
                CONF_AUTO_DISABLED_STATUS_REFRESH, False
            ):
                # Persist auto-disable to config_entry.options. Triggers a
                # listener-driven reload, which is fine - state survives
                # via diag_bucket. Guarded against re-entrance: a service-
                # call retry that still 500s would otherwise trip the
                # threshold every cycle and trigger a redundant reload.
                hass.config_entries.async_update_entry(
                    entry,
                    options={
                        **entry.options,
                        CONF_AUTO_DISABLED_STATUS_REFRESH: True,
                    },
                )
                _LOGGER.warning(
                    "Toyota auto-disabled smart refresh for vin=...%s "
                    "after %d consecutive Layer 1 rejections",
                    vin[-6:],
                    state.consecutive_post_rejections,
                )
            # Fall back to a bare GET so /status entities still refresh
            # this cycle (matches the HARD_DISABLED legacy path). Useful
            # for cycles before auto-disable kicks in, and for any vehicle
            # whose POST 500s but whose /status still serves stale-cache
            # data we can read. Suppression list matches the POST's so
            # transient connectivity issues during the fallback don't
            # abort _refresh_one_vehicle's bookkeeping either.
            with contextlib.suppress(
                ToyotaApiError,
                httpx.ConnectTimeout,
                httpcore.ConnectTimeout,
                asyncioexceptions.TimeoutError,
                httpx.ReadTimeout,
            ):
                await _call_tagged(
                    "status_after_post_fail",
                    vin,
                    vehicle.update(only=["status"]),
                )
            return
        on_post_layer1_success(state)
        # Auto-recovery from HARD_DISABLED_AUTO: a successful POST proves
        # the gateway can process this endpoint. Lift the flag so the
        # strategy goes back to ACTIVE on the next cycle. Triggered by
        # service-call bypass (the user explicitly retrying via the
        # refresh button) or by a transient 5xx clearing on its own.
        if entry.options.get(CONF_AUTO_DISABLED_STATUS_REFRESH, False):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    CONF_AUTO_DISABLED_STATUS_REFRESH: False,
                },
            )
            _LOGGER.info(
                "Toyota auto-disable cleared for vin=...%s after successful POST",
                vin[-6:],
            )

        # Layer 2: poll for occurrence_date advancement.
        deadline = dt_util.now() + timedelta(seconds=timeout_s)
        previous_occurrence = state.last_status_occurrence_date
        while dt_util.now() < deadline:
            await asyncio.sleep(10)
            try:
                await _call_tagged(
                    "post_status_poll", vin, vehicle.update(only=["status"])
                )
            except (
                ToyotaApiError,
                httpx.ConnectTimeout,
                httpcore.ConnectTimeout,
                asyncioexceptions.TimeoutError,
                httpx.ReadTimeout,
            ):
                # 429s and timeouts here are expected mid-wake; loop again.
                continue
            status_data = vehicle._endpoint_data.get("status")  # noqa: SLF001
            occ = (
                getattr(getattr(status_data, "payload", None), "occurrence_date", None)
                if status_data is not None
                else None
            )
            if occ is not None:
                state.last_status_fetch_at = dt_util.now()
                if previous_occurrence is None or occ > previous_occurrence:
                    on_occurrence_advanced(state, occ)
                    return
        # Loop expired without advancement.
        on_wake_failed(state, opts)
        if state.soft_disabled:
            _LOGGER.warning(
                "Toyota soft-disabled status refresh for vin=...%s "
                "(%d consecutive failed wakes)",
                vin[-6:],
                state.consecutive_failed_wakes,
            )

    async def _enact_decision(
        vehicle: Vehicle,
        vin: str,
        state: VinState,
        decision: RefreshDecision,
        wake_timeout_s: int = STRATEGY_DEFAULT_WAKE_TIMEOUT_S,
    ) -> None:
        """Execute the per-action /status path for one VIN.

        POST_THEN_GET also manages the cycle-based followup counter per the
        strategy's caller contract: JUST_STOPPED initialises it, followup
        cycles decrement, SERVICE_CALL / IDLE_WAKE leave it alone.

        ``wake_timeout_s`` is forwarded to :func:`_execute_post_then_get` for
        the SERVICE_CALL trigger (carrying the user-supplied timeout from
        services.yaml). All other triggers fall through to the default.
        """
        if decision.action is RefreshAction.POST_THEN_GET:
            if decision.trigger is RefreshTrigger.JUST_STOPPED:
                state.remaining_post_cycles = max(0, post_count_per_stop - 1)
            elif decision.trigger is RefreshTrigger.JUST_STOPPED_FOLLOWUP:
                state.remaining_post_cycles = max(0, state.remaining_post_cycles - 1)
            await _execute_post_then_get(vehicle, vin, state, wake_timeout_s)
        elif decision.action is RefreshAction.GET_ONLY:
            await _execute_get_only(vehicle, vin, state)
        elif decision.action is RefreshAction.HARD_DISABLED:
            # Legacy path: include /status in the standard sweep.
            with contextlib.suppress(ToyotaApiError, httpx.ReadTimeout):
                await _call_tagged(
                    "status_legacy", vin, vehicle.update(only=["status"])
                )
        # SERVE_FROM_CACHE: no new fetch; the cached response gets re-injected
        # via _persist_status_for_cache() below.

    async def _execute_get_only(vehicle: Vehicle, vin: str, state: VinState) -> None:
        """Issue GET /status only.

        Updates ``state.last_status_fetch_at`` on success and advances the
        cached occurrence_date if the gateway returned a newer one. Stale-cache
        429s and read-timeouts are swallowed: the rest of vehicle data is fresh
        and LockStatus serves from the previous cycle's cached value.
        """
        with contextlib.suppress(ToyotaApiError, httpx.ReadTimeout):
            await _call_tagged("status_only", vin, vehicle.update(only=["status"]))
            status_data = vehicle._endpoint_data.get("status")  # noqa: SLF001
            occ = (
                getattr(
                    getattr(status_data, "payload", None),
                    "occurrence_date",
                    None,
                )
                if status_data
                else None
            )
            if occ is not None:
                state.last_status_fetch_at = dt_util.now()
                if (
                    state.last_status_occurrence_date is None
                    or occ > state.last_status_occurrence_date
                ):
                    on_occurrence_advanced(state, occ)

    def _persist_status_for_cache(vehicle: Vehicle, vin: str) -> None:
        """Mirror this cycle's /status response into the diag-bucket cache.

        If the cycle fetched /status, snapshot it for future SERVE_FROM_CACHE
        cycles. If it didn't (POST flow skipped, or cache-only this cycle),
        re-inject the previously cached response into this cycle's fresh
        Vehicle so lock/door/window sensors keep their last-known values
        instead of falling to "unknown".
        """
        cached_status = vehicle._endpoint_data.get("status")  # noqa: SLF001
        if cached_status is not None:
            diag_bucket["last_status_response_per_vin"][vin] = cached_status
            return
        cached = diag_bucket["last_status_response_per_vin"].get(vin)
        if cached is not None:
            vehicle._endpoint_data["status"] = cached  # noqa: SLF001

    async def _refresh_one_vehicle(vehicle: Vehicle) -> VehicleData:
        """Fetch one vehicle's full data.

        Does NOT catch exceptions; caller decides retain-vs-propagate policy
        based on config toggle. Each pytoyoda call is tagged via _call_tagged
        so per-endpoint failure distribution is visible in the log.

        Smart-refresh strategy (see refresh_strategy.py) gates whether we
        issue a POST /refresh-status and how we fetch /status. The strategy
        is consulted AFTER vehicle.update() runs so it can see this cycle's
        odometer for movement detection.
        """
        vin = vehicle.vin

        # Phase 1: fetch every endpoint EXCEPT /status. The strategy decides
        # the /status path below; calling it inside vehicle.update() risks a
        # 429+APIGW-403 from a stale cache that we can avoid entirely by
        # POSTing first OR by serving from cache. Note: vehicle.update() also
        # populates _endpoint_data["telemetry"], which we read for odometer.
        await _call_tagged("vehicle.update", vin, vehicle.update(skip=["status"]))

        # Build snapshot for the strategy.
        current_odometer_km: float | None = None
        try:
            telemetry = vehicle._endpoint_data.get("telemetry")  # noqa: SLF001
            payload = getattr(telemetry, "payload", None)
            odo_obj = getattr(payload, "odometer", None) if payload else None
            if odo_obj is not None and odo_obj.value is not None:
                current_odometer_km = float(odo_obj.value)
        except (AttributeError, TypeError, ValueError):
            current_odometer_km = None

        state = _build_vin_state(vin) if vin else VinState()
        # pending_service_calls maps VIN to the user-supplied wake timeout
        # in seconds. Presence in the dict means "service call pending"; the
        # value is forwarded to _execute_post_then_get for the wake budget.
        service_timeout = pending_service_calls.pop(vin, None) if vin else None
        service_pending = service_timeout is not None
        decision = decide(
            CycleSnapshot(
                now=dt_util.now(),
                current_odometer_km=current_odometer_km,
                state=state,
                options=_strategy_options(),
                user_service_call_pending=service_pending,
            )
        )
        _LOGGER.debug(
            "smart_strategy vin=...%s action=%s trigger=%s service_pending=%s "
            "soft_disabled=%s pending_keys=%s",
            (vin or "")[-6:],
            decision.action.value,
            decision.trigger.value,
            service_pending,
            state.soft_disabled,
            list(pending_service_calls.keys()),
        )

        # Phase 2: enact the /status decision.
        if vin:
            wake_timeout_s = (
                service_timeout
                if service_timeout is not None
                else STRATEGY_DEFAULT_WAKE_TIMEOUT_S
            )
            await _enact_decision(vehicle, vin, state, decision, wake_timeout_s)

        if vin:
            _persist_status_for_cache(vehicle, vin)

        # Movement / sensor state.
        car_currently_moving = (
            state.last_odometer_km is not None
            and current_odometer_km is not None
            and current_odometer_km != state.last_odometer_km
        )
        state.last_odometer_km = current_odometer_km
        state.was_moving_last_cycle = car_currently_moving

        # Persist diagnostic state-name for the sensor + commit the rest.
        if vin:
            diag_bucket["last_status_refresh_state_per_vin"][vin] = (
                decision.refresh_state.value
            )
            diag_bucket["last_status_refresh_trigger_per_vin"][vin] = (
                decision.trigger.value
            )
            _persist_vin_state(vin, state)

        statistics: StatisticsData | None = None
        if vin is not None:
            # Serialised to avoid Toyota burst rate-limit. Firing these four
            # summary calls in an asyncio.gather within the same event-loop
            # tick reliably trips a 429 with {"description": "Unauthorized"}
            # response bodies. See pytoyoda/ha_toyota#282.
            statistics = StatisticsData(
                day=await _call_tagged(
                    "day_summary", vin, vehicle.get_current_day_summary()
                ),
                week=await _call_tagged(
                    "week_summary", vin, vehicle.get_current_week_summary()
                ),
                month=await _call_tagged(
                    "month_summary", vin, vehicle.get_current_month_summary()
                ),
                year=await _call_tagged(
                    "year_summary", vin, vehicle.get_current_year_summary()
                ),
            )
        now = dt_util.now()
        # NB: do NOT update last_fetch_time_per_vin here. We need commit
        # semantics matching coordinator.data: if a later vehicle in the loop
        # fails and we raise UpdateFailed, this vehicle's data is not visible
        # to sensors - so its fetch timestamp must also not be visible, or
        # users see the inconsistent "entity unavailable AND last fetch
        # 3 minutes ago" state. The caller updates last_fetch_time_per_vin
        # only after the whole refresh has committed.
        err = last_error_per_vin.get(vehicle.vin) if vehicle.vin else None
        return VehicleData(
            data=vehicle,
            statistics=statistics,
            metric_values=metric_values,
            last_successful_fetch=now,
            last_error_time=err[0] if err else None,
            last_error_code=err[1] if err else None,
            is_cached=False,
        )

    async def async_get_vehicle_data() -> list[VehicleData] | None:  # noqa: C901, PLR0912
        """Fetch vehicle data from Toyota API, per-car error handling.

        Branch count is intentional: each except-arm maps to a distinct
        recovery policy (retain-cache vs propagate UpdateFailed) for either
        the fleet-level get_vehicles call or the per-vehicle refresh. Folding
        them into a helper would obscure the recovery semantics.
        """
        # Step 1: get the vehicle list. This is account-level; if it fails
        # we have no per-vehicle recovery path, but we CAN serve stale
        # fleet data if any exists.
        try:
            vehicles = await asyncio.wait_for(client.get_vehicles(), 15)
        except ToyotaLoginError:
            # Credentials invalid - not transient, surface as auth error.
            _LOGGER.exception("Toyota login error")
            return None
        except (
            ToyotaApiError,
            httpx.ConnectTimeout,
            httpcore.ConnectTimeout,
            asyncioexceptions.CancelledError,
            asyncioexceptions.TimeoutError,
            httpx.ReadTimeout,
        ) as ex:
            code = _error_code(ex)
            now = dt_util.now()
            for vin in last_good_per_vin:
                last_error_per_vin[vin] = (now, code)
            if retain_on_transient and last_good_per_vin:
                _LOGGER.warning(
                    "Toyota get_vehicles failed (%s); using cached fleet data", code
                )
                return [
                    _build_vehicle_data_from_cache(vin) for vin in last_good_per_vin
                ]
            msg = f"Toyota get_vehicles failed: {ex}"
            raise UpdateFailed(msg) from ex
        except ValidationError:
            _LOGGER.exception("Toyota validation error on get_vehicles")
            code = "validation error"
            now = dt_util.now()
            for vin in last_good_per_vin:
                last_error_per_vin[vin] = (now, code)
            if retain_on_transient and last_good_per_vin:
                return [
                    _build_vehicle_data_from_cache(vin) for vin in last_good_per_vin
                ]
            return None

        # Step 2: fetch each vehicle's data independently, so a failure on
        # one does not drop the others. Per-vehicle error recovery honors
        # the retain-on-transient toggle.
        vehicle_informations: list[VehicleData] = []
        for vehicle in vehicles or []:
            if not vehicle or vehicle.vin is None:
                continue
            vin = vehicle.vin
            try:
                vehicle_data = await _refresh_one_vehicle(vehicle)
                last_good_per_vin[vin] = vehicle_data
                vehicle_informations.append(vehicle_data)
            except (
                ToyotaApiError,
                ToyotaInternalError,
                httpx.ConnectTimeout,
                httpcore.ConnectTimeout,
                asyncioexceptions.CancelledError,
                asyncioexceptions.TimeoutError,
                httpx.ReadTimeout,
                ValidationError,
            ) as ex:
                code = _error_code(ex)
                last_error_per_vin[vin] = (dt_util.now(), code)
                _LOGGER.warning(
                    "Toyota refresh failed for vin=...%s (%s)", vin[-6:], code
                )
                if retain_on_transient and vin in last_good_per_vin:
                    # retain=ON + cache available: serve stale cached data.
                    vehicle_informations.append(_build_vehicle_data_from_cache(vin))
                else:
                    # retain=OFF OR retain=ON with no cache yet: emit a stub
                    # VehicleData. The Vehicle object came from get_vehicles()
                    # so it has identity (vin, alias, device info) but no
                    # endpoint data because vehicle.update() failed. Data
                    # sensors read through a ToyotaBaseEntity.available
                    # override that checks last_successful_fetch, so stubs
                    # render as unavailable without raising UpdateFailed for
                    # the whole refresh. Siblings that succeeded this cycle
                    # keep their fresh data - per-vehicle fault isolation.
                    vehicle_informations.append(
                        VehicleData(
                            data=vehicle,
                            statistics=None,
                            metric_values=metric_values,
                            last_successful_fetch=None,
                            last_error_time=dt_util.now(),
                            last_error_code=code,
                            is_cached=False,
                        )
                    )

        # If nothing useful to serve (no fresh fetch anywhere, no cache either),
        # match upstream behaviour: raise UpdateFailed so the coordinator flips
        # last_update_success=False and all data sensors become unavailable.
        # Diag sensors stay visible via their own always_available override.
        any_served = any(
            vd.get("is_cached") or vd.get("last_successful_fetch") is not None
            for vd in vehicle_informations
        )
        if not any_served:
            msg = "Toyota refresh failed for all vehicles"
            raise UpdateFailed(msg)

        # Commit per-VIN fetch timestamps now that the refresh has survived
        # all exception paths. This is the only place last_fetch_time_per_vin
        # is written to keep it consistent with coordinator.data: both are
        # updated iff the whole refresh succeeds. Cached entries (from the
        # retain=ON path) carry None last_successful_fetch and are skipped.
        for vd in vehicle_informations:
            if vd.get("is_cached"):
                continue
            vin = vd["data"].vin if vd.get("data") else None
            fetched = vd.get("last_successful_fetch")
            if vin and fetched is not None:
                last_fetch_time_per_vin[vin] = fetched

        _LOGGER.debug(vehicle_informations)
        return vehicle_informations

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_get_vehicle_data,
        update_interval=timedelta(minutes=polling_interval_minutes),
    )

    # Attach the per-VIN diagnostic dicts to the coordinator so sensors can read
    # them even when coordinator.data is stale after UpdateFailed. The dicts are
    # updated in the refresh function's exception handlers BEFORE UpdateFailed
    # fires, so they carry the freshest error/timestamp info irrespective of
    # the retain_on_transient toggle. Diag sensors bind via
    # getattr(coordinator, "_diag_last_fetch_per_vin" / "_diag_last_error_per_vin").
    coordinator._diag_last_fetch_per_vin = last_fetch_time_per_vin  # noqa: SLF001
    coordinator._diag_last_error_per_vin = last_error_per_vin  # noqa: SLF001
    coordinator._diag_status_occurrence_per_vin = diag_bucket[  # noqa: SLF001
        "last_status_occurrence_date_per_vin"
    ]
    coordinator._diag_status_refresh_state_per_vin = diag_bucket[  # noqa: SLF001
        "last_status_refresh_state_per_vin"
    ]

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await _async_register_services(hass)

    return True


SERVICE_REFRESH_VEHICLE_STATUS = "refresh_vehicle_status"
ATTR_TIMEOUT_SECONDS = "timeout_seconds"


def _resolve_devices_to_vins_per_entry(
    hass: HomeAssistant, device_ids: list[str]
) -> dict[str, list[str]]:
    """Group VINs by config entry id for the requested device targets.

    Devices not registered, devices without a Toyota VIN identifier, and
    devices whose entry has been unloaded are silently skipped.
    """
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    device_reg = dr.async_get(hass)
    per_entry_vins: dict[str, list[str]] = {}
    for device_id in device_ids:
        device = device_reg.async_get(device_id)
        if device is None:
            continue
        vin = next(
            (
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN
            ),
            None,
        )
        if not vin:
            continue
        for entry_id in device.config_entries:
            if entry_id in hass.data.get(DOMAIN, {}):
                per_entry_vins.setdefault(entry_id, []).append(vin)
    return per_entry_vins


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register the toyota.refresh_vehicle_status service exactly once.

    Service handlers resolve their target devices to VINs via the device
    registry, set a per-VIN flag in each entry's diag bucket, and trigger
    an immediate coordinator refresh. The smart strategy then picks up
    user_service_call_pending=True on the next cycle (within seconds), so
    the wake POST happens out-of-band of the normal polling cadence.
    """
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_VEHICLE_STATUS):
        return

    async def _handle_refresh_vehicle_status(call: ServiceCall) -> None:
        # device_id arrives either as a string (when called with
        # data={"device_id":...}) or a list (target.device normalization).
        raw = call.data.get("device_id") or []
        device_ids: list[str] = [raw] if isinstance(raw, str) else list(raw)
        if not device_ids:
            _LOGGER.warning(
                "toyota.refresh_vehicle_status called with no device target"
            )
            return
        # Pull the user-supplied wake-poll budget. services.yaml constrains
        # this to 10..180 with a default of 60; we mirror that default here
        # in case the call somehow arrives without the field.
        timeout_seconds = int(call.data.get(ATTR_TIMEOUT_SECONDS, 60))
        _LOGGER.info(
            "toyota.refresh_vehicle_status invoked for devices=%s (timeout=%ds)",
            device_ids,
            timeout_seconds,
        )
        per_entry_vins = _resolve_devices_to_vins_per_entry(hass, device_ids)
        for entry_id, vins in per_entry_vins.items():
            entry_diag = hass.data[DOMAIN].get(f"{entry_id}_diag")
            if entry_diag is None:
                continue
            pending = entry_diag.setdefault("pending_service_calls", {})
            for vin in vins:
                pending[vin] = timeout_seconds
            coord = hass.data[DOMAIN].get(entry_id)
            if coord is not None:
                # Schedule an immediate refresh; the strategy will read the
                # pending flag and POST. We don't await here because the
                # refresh chain can take ~25s (poll loop) and HA service
                # calls block the calling automation by default.
                hass.async_create_task(coord.async_request_refresh())

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_VEHICLE_STATUS,
        _handle_refresh_vehicle_status,
    )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change so the new toggle takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
