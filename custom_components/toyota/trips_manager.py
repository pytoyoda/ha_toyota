"""Recent-trips manager: glues TripsCacheStore + transform + fetch logic.

One manager per config entry. Lifecycle:

- Setup: ``async setup()`` loads the on-disk cache. Entry-scoped state lives
  in ``hass.data[DOMAIN][f"{entry_id}_trips_state"]`` and tracks per-VIN
  followup-pending flags.
- Per-VIN per-cycle: coordinator calls ``async_maybe_refresh(vehicle, vin,
  decision)`` after ``_enact_decision`` returns. Manager decides whether to
  fetch based on the decision's trigger (just_stopped / followup) and the
  cache state (cold start vs steady state).
- Service / button: ``async_service_refresh(vin, vehicle, limit)`` discards
  the cache for that VIN and refetches ``limit`` trips, populating the
  cache. Works regardless of ``max_recent_trips`` config.

Pure orchestration; the actual transform lives in trips_transform.py and the
storage in trips_cache.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .refresh_strategy import RefreshTrigger
from .trips_cache import TripsCacheStore
from .trips_transform import to_card_shape

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from pytoyoda.models.vehicle import Vehicle

    from .refresh_strategy import RefreshDecision

_LOGGER = logging.getLogger(__name__)

# How many trips to fetch on the steady-state delta-check path. Two is the
# minimum that lets us distinguish "one new trip since last cycle" from "gap
# of two or more new trips since last cycle"; the latter triggers a full
# refill via ``max_recent_trips``.
DELTA_FETCH_LIMIT = 2


class RecentTripsManager:
    """Per-config-entry orchestrator for the recent-trips sensor data path.

    Independent of the existing cycle's ``trip_history`` endpoint (which
    stays at limit=1, route=False for backward compatibility). This manager
    issues separate ``Vehicle.get_recent_trips()`` calls when configured.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, max_recent_trips: int
    ) -> None:
        """Bind the manager to a config entry and current options."""
        self._hass = hass
        self._entry_id = entry.entry_id
        self._max = max_recent_trips
        self._cache = TripsCacheStore(hass, entry.entry_id)
        # In-memory state: per-VIN dict tracking whether a JUST_STOPPED tick
        # produced no new trip (so we should also fetch on JUST_STOPPED_FOLLOWUP).
        # Lost on restart; that's fine since on restart we have no signal to
        # need a followup retry until the next stop event anyway.
        self._followup_pending: dict[str, bool] = {}
        # Per-VIN flag: cache was loaded under-filled (fewer trips than current
        # max, after trim). Set in async_setup, cleared in async_maybe_refresh
        # after a one-shot refill. Covers the user-raised-max case (e.g. 5 -> 10
        # via options flow) where the cache survives the reload but at the old
        # smaller size; we want the next refresh tick to top it back up.
        self._underfilled_vins: set[str] = set()

    async def async_setup(self) -> None:
        """Load the on-disk cache, trim VINs over max, mark under-filled VINs.

        Trim covers the case where ``max_recent_trips`` was lowered via the
        options flow (5 -> 3): cache shrinks. Marking under-filled VINs
        covers the inverse - max raised (5 -> 10): the trim is a no-op but
        we want to seed back up to the new ceiling on the next refresh tick.
        Idempotent; safe to call repeatedly.
        """
        await self._cache.load()
        mutated = False
        for vin in list(self._cache.known_vins()):
            before = len(self._cache.get(vin))
            self._cache.trim(vin, self._max)
            after = len(self._cache.get(vin))
            if after != before:
                mutated = True
            if 0 < after < self._max:
                self._underfilled_vins.add(vin)
        if mutated:
            await self._cache.save()

    @property
    def cache(self) -> TripsCacheStore:
        """Direct access to the cache store. Read-only contract for sensors."""
        return self._cache

    @property
    def max_recent_trips(self) -> int:
        """Current configured cap. 0 means auto-fetch is disabled."""
        return self._max

    def update_max(self, new_max: int) -> bool:
        """React to options-flow change in ``max_recent_trips``.

        Lowered (10 -> 5): trim cache to new size, no refetch.
        Raised  (5 -> 10): leave cache alone; coordinator's next maybe_refresh
            will see ``len(cache) < new_max`` and seed the gap.
        Set to 0: drop entries entirely; sensor reports 0 trips, auto-fetch off.

        Returns True if the cache was mutated (caller should persist).
        """
        if new_max == self._max:
            return False
        old_max = self._max
        self._max = new_max
        if new_max < old_max:
            for vin in self._cache.known_vins():
                self._cache.trim(vin, new_max)
            return True
        # Raised: nothing to do here; the next refresh tick will fill via
        # the cache-cold-start branch in async_maybe_refresh.
        return False

    async def async_maybe_refresh(
        self,
        vehicle: Vehicle,
        vin: str,
        decision: RefreshDecision,
    ) -> None:
        """Fetch trips per the rolling-cache + delta lifecycle.

        Called once per VIN per coordinator cycle. No-op when disabled.
        """
        if self._max <= 0:
            return

        # Cold start (cache empty for this VIN): seed with limit=max trips.
        # This covers fresh integration install, fresh-enable post-options-flow,
        # and post-clear-cache scenarios. Independent of trigger.
        if not self._cache.get(vin):
            await self._seed_cache(vehicle, vin, self._max)
            return

        # Under-filled cache (cache was already on disk with fewer trips than
        # the current max, typically because the user just raised max via
        # options-flow). Seed once with limit=max to refill, then drop the
        # underfill flag so subsequent ticks fall through to normal delta-fetch.
        if vin in self._underfilled_vins:
            await self._seed_cache(vehicle, vin, self._max)
            self._underfilled_vins.discard(vin)
            return

        # Steady state: fetch only on stop-event triggers.
        # JUST_STOPPED: primary trigger.
        # JUST_STOPPED_FOLLOWUP: only if the prior just_stopped fetch yielded
        # no new trip (Toyota hadn't processed it yet).
        trigger = decision.trigger

        if trigger is RefreshTrigger.JUST_STOPPED:
            yielded_new = await self._delta_fetch(vehicle, vin)
            self._followup_pending[vin] = not yielded_new
        elif (
            trigger is RefreshTrigger.JUST_STOPPED_FOLLOWUP
            and self._followup_pending.get(vin)
        ):
            yielded_new = await self._delta_fetch(vehicle, vin)
            self._followup_pending[vin] = (
                False if yielded_new else self._followup_pending.get(vin, False)
            )
        # All other triggers: no-op. Trips don't change between drives.

    async def async_service_refresh(
        self, vin: str, vehicle: Vehicle, limit: int
    ) -> int:
        """Discard cache for VIN, fetch ``limit`` trips, populate cache.

        Used by the ``toyota.refresh_recent_trips`` service and the per-vehicle
        ``Refresh recent trips`` button. Works regardless of ``max_recent_trips``
        config (so users with auto-fetch off can still drive fetches via
        automations).

        Returns the count of trips placed in cache.
        """
        if not 1 <= limit <= 50:  # noqa: PLR2004
            msg = f"limit must be between 1 and 50, got {limit}"
            raise ValueError(msg)
        self._cache.clear(vin)
        await self._seed_cache(vehicle, vin, limit)
        return len(self._cache.get(vin))

    async def _seed_cache(self, vehicle: Vehicle, vin: str, limit: int) -> None:
        """Fetch ``limit`` trips with route, transform, populate cache."""
        try:
            trips = await vehicle.get_recent_trips(limit=limit, with_route=True)
        except Exception:
            _LOGGER.exception(
                "Toyota recent-trips seed-fetch failed for vin=...%s", vin[-6:]
            )
            return
        card_trips: list[dict] = []
        alias = vehicle.alias if hasattr(vehicle, "alias") else None
        for t in trips:
            raw = self._raw_trip_dict(t)
            if raw is None:
                continue
            shape = to_card_shape(raw, alias)
            if shape is not None:
                card_trips.append(shape)
        self._cache.set(vin, card_trips)
        await self._cache.save()
        _LOGGER.debug(
            "Toyota recent-trips seeded vin=...%s with %d trips (limit=%d)",
            vin[-6:],
            len(card_trips),
            limit,
        )

    async def _delta_fetch(self, vehicle: Vehicle, vin: str) -> bool:
        """Fetch the most recent N trips (DELTA_FETCH_LIMIT), dedup, append.

        Returns True if at least one new trip was added to the cache, False
        otherwise (caller uses this to decide whether followup retry needed).
        """
        try:
            trips = await vehicle.get_recent_trips(
                limit=DELTA_FETCH_LIMIT, with_route=True
            )
        except Exception:
            _LOGGER.exception(
                "Toyota recent-trips delta-fetch failed for vin=...%s", vin[-6:]
            )
            return False
        if not trips:
            return False

        alias = vehicle.alias if hasattr(vehicle, "alias") else None
        # Walk newest-first; collect shapes that are actually new.
        new_shapes: list[dict] = []
        seen_existing = False
        for t in trips:
            raw = self._raw_trip_dict(t)
            if raw is None:
                continue
            trip_id = raw.get("id")
            if trip_id and self._cache.has_trip_id(vin, trip_id):
                seen_existing = True
                break
            shape = to_card_shape(raw, alias)
            if shape is not None:
                new_shapes.append(shape)

        if not new_shapes:
            # All returned trips are already cached; nothing new.
            return False

        if not seen_existing and len(new_shapes) >= DELTA_FETCH_LIMIT:
            # Both fetched trips are new and we didn't see any cached one in
            # the overlap. Gap detected (HA was down or we missed cycles);
            # fall back to a full reseed to catch up.
            _LOGGER.info(
                "Toyota recent-trips gap detected for vin=...%s, refilling", vin[-6:]
            )
            await self._seed_cache(vehicle, vin, self._max)
            return True

        # Normal case: prepend the new ones (they came back newest-first).
        for shape in reversed(new_shapes):
            # Reversed so the newest ends up at index 0 after sequential prepends.
            self._cache.append(vin, shape, self._max)
        await self._cache.save()
        _LOGGER.debug(
            "Toyota recent-trips delta vin=...%s appended %d new trip(s)",
            vin[-6:],
            len(new_shapes),
        )
        return True

    @staticmethod
    def _raw_trip_dict(trip_obj) -> dict | None:  # type: ignore[no-untyped-def]  # noqa: ANN001
        """Extract the raw _TripModel dict from a pytoyoda Trip wrapper.

        pytoyoda's Trip wrapper holds the underlying _TripModel at ``_trip``.
        We use ``.model_dump(by_alias=True)`` to get the JSON-shape with
        Toyota's native camelCase keys (matching what ``to_card_shape``
        expects). Returns None on any access failure.
        """
        try:
            inner = getattr(trip_obj, "_trip", None)
            if inner is None or not hasattr(inner, "model_dump"):
                return None
        except Exception:  # noqa: BLE001
            return None
        return inner.model_dump(by_alias=True, mode="python")
