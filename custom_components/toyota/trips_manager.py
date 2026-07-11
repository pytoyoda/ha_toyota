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

import asyncio
import logging
from typing import TYPE_CHECKING

from .refresh_strategy import RefreshTrigger
from .trips_cache import TripsCacheStore
from .trips_transform import to_card_shape

if TYPE_CHECKING:
    from collections.abc import Iterable

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
        # Per-VIN locks serialise cache mutation paths (cold-start seed,
        # delta-fetch, service refresh). Without them a coordinator stop tick
        # racing a service/button call can issue duplicate get_recent_trips
        # calls and last-writer-wins on the cache + _followup_pending state.
        self._locks: dict[str, asyncio.Lock] = {}

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

    def _lock(self, vin: str) -> asyncio.Lock:
        """Per-VIN lock; created on first use."""
        lock = self._locks.get(vin)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[vin] = lock
        return lock

    async def async_prune_orphans(self, known_vins: Iterable[str]) -> bool:
        """Drop cached VINs not in ``known_vins`` and persist if mutated.

        Called by the coordinator after the first successful refresh, so
        VINs the user removed from their Toyota account stop accumulating
        in the on-disk cache.
        """
        mutated = self._cache.prune_to(known_vins)
        if mutated:
            await self._cache.save()
        return mutated

    def _piggyback_latest_id(self, vehicle: Vehicle) -> str | None:
        """Latest trip id from the cycle's free ``trip_history`` signal.

        ``trip_history`` is already populated by the cycle's
        `/v1/trips?summary=True&limit=1&route=False` call, so reading it
        costs nothing. Returns None when auto-fetch should be skipped:

        - feature disabled (``max_recent_trips <= 0``);
        - ``trip_history is None``: endpoint failed this cycle (now
          optional in pytoyoda) - conservative skip, the service-refresh
          button still works;
        - ``trip_history == []``: vehicle has no trips data at all (e.g.
          AYGO X on Toyota Connect Lite tier) - no redundant
          ``with_route`` fetch;
        - no id on the record (defensive; production trips have UUIDs).

        Reads via getattr - tests use bare SimpleNamespace stubs that may
        not declare the attribute.
        """
        if self._max <= 0:
            return None
        history = getattr(vehicle, "trip_history", None)
        if not history:
            return None
        return self._extract_trip_id(history[0])

    async def async_maybe_refresh(
        self,
        vehicle: Vehicle,
        vin: str | None,
        decision: RefreshDecision,
    ) -> None:
        """Fetch trips per the rolling-cache + delta lifecycle.

        Called once per VIN per coordinator cycle (vin may be None for a
        vehicle without one; no-op). Gating on the free piggyback signal
        is documented on :meth:`_piggyback_latest_id`. Cache semantics:

        - ``trip_history[0].id == cache[0].id``: cache is in sync with
          Toyota's view. Skip the heavier ``with_route`` fetch; on
          ``JUST_STOPPED`` mark followup-pending in case Toyota's
          summary endpoint is also lagging behind a freshly-driven
          trip.
        - mismatch: there's a new trip we don't have. Cold-start when
          cache empty; delta-fetch on stop triggers; otherwise no-op.
        """
        if not vin:
            return
        latest_id = self._piggyback_latest_id(vehicle)
        if latest_id is None:
            return

        async with self._lock(vin):
            cache = self._cache.get(vin)
            cache_top_id = (
                str(cache[0].get("id")) if cache and cache[0].get("id") else None
            )

            # Cold start: seed with limit=max trips.
            if not cache:
                await self._seed_cache(vehicle, vin, self._max)
                return

            # Under-filled cache (raise-max via options flow). One-shot
            # refill; keep the flag armed if the fetch failed so a later
            # cycle retries instead of leaving the cache short forever.
            if vin in self._underfilled_vins:
                if await self._seed_cache(vehicle, vin, self._max):
                    self._underfilled_vins.discard(vin)
                return

            # Cache top matches Toyota's latest trip - nothing new since
            # last cycle. Skip the heavier with_route fetch; on JUST_STOPPED
            # mark followup-pending so a delayed-ingest trip gets caught
            # next cycle.
            if cache_top_id == latest_id:
                if decision.trigger is RefreshTrigger.JUST_STOPPED:
                    self._followup_pending[vin] = True
                return

            # Mismatch: there's a new trip we don't have. Delta-fetch on
            # stop-event triggers.
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

    @staticmethod
    def _extract_trip_id(trip_obj) -> str | None:  # type: ignore[no-untyped-def]  # noqa: ANN001
        """Pull a stringified trip id off a pytoyoda Trip wrapper.

        ``Trip._trip.id`` is a UUID; cached trip dicts store the same
        value as ``str``. Stringify both ends before comparison so
        UUID/str equality lines up.
        """
        inner = getattr(trip_obj, "_trip", None)
        if inner is None:
            return None
        tid = getattr(inner, "id", None)
        return str(tid) if tid is not None else None

    async def async_service_refresh(
        self, vin: str, vehicle: Vehicle, limit: int
    ) -> int:
        """Replace cache for VIN with ``limit`` freshly fetched trips.

        On fetch failure, prior cache contents are preserved (fail-safe).
        Works regardless of ``max_recent_trips`` config.
        """
        if not 1 <= limit <= 50:  # noqa: PLR2004
            msg = f"limit must be between 1 and 50, got {limit}"
            raise ValueError(msg)
        async with self._lock(vin):
            shapes = await self._fetch_shapes(vehicle, vin, limit)
            if shapes is None:
                # Fetch failed; keep prior cache rather than nuking it.
                return len(self._cache.get(vin))
            self._cache.set(vin, shapes)
            await self._cache.save()
            return len(shapes)

    async def _fetch_shapes(
        self, vehicle: Vehicle, vin: str, limit: int
    ) -> list[dict] | None:
        """Fetch + transform. Returns None on fetch failure, list on success."""
        try:
            trips = await vehicle.get_recent_trips(limit=limit, with_route=True)
        except Exception:
            _LOGGER.exception(
                "Toyota recent-trips fetch failed for vin=...%s", vin[-6:]
            )
            return None
        alias = vehicle.alias if hasattr(vehicle, "alias") else None
        out: list[dict] = []
        for t in trips:
            raw = self._raw_trip_dict(t)
            if raw is None:
                continue
            shape = to_card_shape(raw, alias)
            if shape is not None:
                out.append(shape)
        return out

    async def _seed_cache(self, vehicle: Vehicle, vin: str, limit: int) -> bool:
        """Fetch + commit (set + save); True on success.

        On fetch failure the cache is untouched and False is returned so
        callers can keep retry state (e.g. the underfilled one-shot) armed.
        Caller must hold ``self._lock(vin)``.
        """
        shapes = await self._fetch_shapes(vehicle, vin, limit)
        if shapes is None:
            return False
        self._cache.set(vin, shapes)
        await self._cache.save()
        _LOGGER.debug(
            "Toyota recent-trips seeded vin=...%s with %d trips (limit=%d)",
            vin[-6:],
            len(shapes),
            limit,
        )
        return True

    async def _delta_fetch(self, vehicle: Vehicle, vin: str) -> bool:
        """Fetch DELTA_FETCH_LIMIT trips, dedup, append. Caller holds the lock.

        Returns True if at least one new trip was added to the cache.
        """
        shapes = await self._fetch_shapes(vehicle, vin, DELTA_FETCH_LIMIT)
        if shapes is None or not shapes:
            return False

        # Walk newest-first; collect shapes that are actually new.
        new_shapes: list[dict] = []
        seen_existing = False
        for shape in shapes:
            trip_id = shape.get("id")
            if trip_id and self._cache.has_trip_id(vin, trip_id):
                seen_existing = True
                break
            new_shapes.append(shape)

        if not new_shapes:
            return False

        if not seen_existing and len(new_shapes) >= DELTA_FETCH_LIMIT:
            # Both fetched trips are new with no overlap: gap (HA down or
            # missed cycles); reseed to catch up.
            _LOGGER.info(
                "Toyota recent-trips gap detected for vin=...%s, refilling", vin[-6:]
            )
            await self._seed_cache(vehicle, vin, self._max)
            return True

        # Newest-first; reverse so that sequential prepends end with newest at 0.
        for shape in reversed(new_shapes):
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

        Couples to pytoyoda's private ``_trip`` attribute (manifest pin
        pytoyoda>=5.1.0 guarantees it exists). If pytoyoda exposes a public
        accessor in a future release, switch to it and bump the manifest pin.
        """
        try:
            inner = getattr(trip_obj, "_trip", None)
            if inner is None or not hasattr(inner, "model_dump"):
                return None
        except Exception:  # noqa: BLE001
            return None
        return inner.model_dump(by_alias=True, mode="python")
