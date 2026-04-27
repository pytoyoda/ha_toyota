"""Persistent rolling-window cache of recent trips per vehicle.

Backed by Home Assistant's Store helper, which serialises to JSON under
``.storage/<key>.json`` with atomic writes. One file per config entry,
structured as ``{vin: [trip_dict, ...]}`` where each trip_dict is in the
journey-viewer-card data contract shape (post-transform from pytoyoda's
_TripModel).

The cache survives HA restarts so the dashboard shows trips immediately
on boot rather than waiting for the next drive's smart-refresh tick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CACHE_VERSION = 1
CACHE_KEY_PREFIX = "toyota.trips_cache"


class TripsCacheStore:
    """Per-config-entry rolling cache of recent trips, indexed by VIN.

    Trips are stored in card-shape (post-transform). Identity is the trip's
    Toyota-issued ``id`` field, verified empirically stable across fetches
    in pre-flight task 0c (UUIDs do not change between calls).
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Bind the store to a specific config entry's filesystem slot."""
        self._store: Store[dict[str, list[dict]]] = Store(
            hass, version=CACHE_VERSION, key=f"{CACHE_KEY_PREFIX}.{entry_id}"
        )
        self._data: dict[str, list[dict]] = {}
        self._loaded: bool = False

    async def load(self) -> None:
        """Read the cache from disk. Idempotent; safe to call repeatedly."""
        raw = await self._store.async_load()
        self._data = raw if isinstance(raw, dict) else {}
        self._loaded = True

    async def save(self) -> None:
        """Persist the cache to disk. Caller is responsible for batching."""
        await self._store.async_save(self._data)

    @property
    def loaded(self) -> bool:
        """True once load() has completed at least once."""
        return self._loaded

    def get(self, vin: str) -> list[dict]:
        """Return the cached trips for one VIN, or [] if uninitialised."""
        return list(self._data.get(vin, []))

    def set(self, vin: str, trips: list[dict]) -> None:
        """Replace the cached trips for one VIN.

        Caller is responsible for ordering (most-recent-first by convention)
        and for trimming to ``max_size`` before calling. ``save()`` must be
        called separately to persist.
        """
        self._data[vin] = list(trips)

    def append(self, vin: str, trip: dict, max_size: int) -> None:
        """Prepend a new trip to the front (most-recent-first), trim to max_size.

        No-op if the trip's ``id`` is already in the cache (idempotent;
        protects against double-fetches on retry paths).
        """
        if max_size <= 0:
            return
        existing = self._data.get(vin, [])
        trip_id = trip.get("id")
        if trip_id is not None and any(t.get("id") == trip_id for t in existing):
            return
        # Prepend the new trip, trim the tail.
        self._data[vin] = ([trip, *existing])[:max_size]

    def trim(self, vin: str, max_size: int) -> None:
        """Trim the cached list for one VIN to the first ``max_size`` entries.

        Used when ``max_recent_trips`` is lowered via the options flow.
        """
        if max_size <= 0:
            self._data.pop(vin, None)
            return
        existing = self._data.get(vin)
        if existing is None:
            return
        self._data[vin] = existing[:max_size]

    def has_trip_id(self, vin: str, trip_id: str) -> bool:
        """Return True if any cached trip for VIN has the given id."""
        return any(t.get("id") == trip_id for t in self._data.get(vin, []))

    def clear(self, vin: str) -> None:
        """Drop the entry for one VIN (used by the service call before refetch)."""
        self._data.pop(vin, None)

    def known_vins(self) -> list[str]:
        """Return the list of VINs currently in the cache."""
        return list(self._data.keys())
