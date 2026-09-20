"""Persistent per-config-entry cache of Toyota vehicle-list GUID payloads."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store
from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
from pytoyoda.models.vehicle import Vehicle

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant
    from pytoyoda.api import Api

CACHE_VERSION = 1
CACHE_KEY_PREFIX = "toyota.vehicle_list_cache"


class VehicleListStore:
    """Per-config-entry persisted cache of raw `/v2/vehicle/guid` payloads."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Bind the store to one config entry's filesystem slot."""
        self._store: Store[dict[str, list[dict[str, Any]]]] = Store(
            hass, version=CACHE_VERSION, key=f"{CACHE_KEY_PREFIX}.{entry_id}"
        )
        self._vehicles: list[dict[str, Any]] = []
        self._loaded: bool = False

    async def load(self) -> None:
        """Read the persisted vehicle list from disk."""
        raw = await self._store.async_load()
        self._vehicles = (
            copy.deepcopy(raw.get("vehicles", [])) if isinstance(raw, dict) else []
        )
        self._loaded = True

    async def save(self) -> None:
        """Persist the current vehicle list to disk."""
        await self._store.async_save({"vehicles": self._vehicles})

    @property
    def loaded(self) -> bool:
        """True once load() has completed at least once."""
        return self._loaded

    def get(self) -> list[dict[str, Any]]:
        """Return a deep copy of the cached raw vehicle payloads."""
        return copy.deepcopy(self._vehicles)

    def set(self, vehicles: list[dict[str, Any]]) -> None:
        """Replace the cached raw vehicle payload list."""
        self._vehicles = copy.deepcopy(vehicles)

    def replace_from_vehicles(self, vehicles: Iterable[Vehicle]) -> None:
        """Persist the raw VehicleGuidModel payload for each live Vehicle."""
        self._vehicles = []
        for vehicle in vehicles:
            vehicle_info = vehicle._vehicle_info  # noqa: SLF001
            self._vehicles.append(vehicle_info.model_dump(mode="json", by_alias=True))

    def rebuild_vehicles(self, api: Api, *, metric: bool) -> list[Vehicle]:
        """Rebuild real pytoyoda Vehicle objects from the cached raw payloads."""
        return [
            Vehicle(api, VehicleGuidModel.model_validate(vehicle), metric=metric)
            for vehicle in self._vehicles
        ]
