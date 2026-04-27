"""Tests for the per-config-entry rolling trips cache (TripsCacheStore)."""

from __future__ import annotations

import pytest

from custom_components.toyota.trips_cache import TripsCacheStore


def _trip(trip_id: str, label: str = "") -> dict:
    """Minimal card-shape trip dict for cache tests."""
    return {"id": trip_id, "label": label or trip_id}


@pytest.mark.asyncio
async def test_load_uninitialised_returns_empty(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    assert cache.loaded is True
    assert cache.get("VIN1") == []
    assert cache.known_vins() == []


@pytest.mark.asyncio
async def test_set_and_get_round_trip(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b"), _trip("c")])
    assert cache.get("VIN1") == [_trip("a"), _trip("b"), _trip("c")]
    # get returns a copy; mutating it doesn't affect the cache
    got = cache.get("VIN1")
    got.append(_trip("z"))
    assert cache.get("VIN1") == [_trip("a"), _trip("b"), _trip("c")]


@pytest.mark.asyncio
async def test_set_and_save_persists_across_instances(hass):
    cache1 = TripsCacheStore(hass, "entry1")
    await cache1.load()
    cache1.set("VIN1", [_trip("a"), _trip("b")])
    await cache1.save()

    cache2 = TripsCacheStore(hass, "entry1")
    await cache2.load()
    assert cache2.get("VIN1") == [_trip("a"), _trip("b")]


@pytest.mark.asyncio
async def test_per_entry_isolation(hass):
    """Two entries get separate stores, not shared state."""
    cache_a = TripsCacheStore(hass, "entry_a")
    cache_b = TripsCacheStore(hass, "entry_b")
    await cache_a.load()
    await cache_b.load()
    cache_a.set("VIN1", [_trip("a-only")])
    cache_b.set("VIN1", [_trip("b-only")])
    await cache_a.save()
    await cache_b.save()

    cache_a2 = TripsCacheStore(hass, "entry_a")
    cache_b2 = TripsCacheStore(hass, "entry_b")
    await cache_a2.load()
    await cache_b2.load()
    assert cache_a2.get("VIN1") == [_trip("a-only")]
    assert cache_b2.get("VIN1") == [_trip("b-only")]


@pytest.mark.asyncio
async def test_multi_vin_in_one_entry(hass):
    """One config entry, two VINs, independent slots."""
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("v1-a")])
    cache.set("VIN2", [_trip("v2-a"), _trip("v2-b")])
    assert cache.get("VIN1") == [_trip("v1-a")]
    assert cache.get("VIN2") == [_trip("v2-a"), _trip("v2-b")]
    assert sorted(cache.known_vins()) == ["VIN1", "VIN2"]


@pytest.mark.asyncio
async def test_append_prepends_and_trims(hass):
    """append() puts new trip at front, trims to max_size from the back."""
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b"), _trip("c")])
    cache.append("VIN1", _trip("z"), max_size=3)
    assert cache.get("VIN1") == [_trip("z"), _trip("a"), _trip("b")]


@pytest.mark.asyncio
async def test_append_below_max_does_not_trim(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b")])
    cache.append("VIN1", _trip("z"), max_size=5)
    assert cache.get("VIN1") == [_trip("z"), _trip("a"), _trip("b")]


@pytest.mark.asyncio
async def test_append_dedupes_existing_id(hass):
    """Re-appending the same trip ID is a no-op (idempotent)."""
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b")])
    cache.append("VIN1", _trip("a", label="duplicate"), max_size=5)
    assert cache.get("VIN1") == [_trip("a"), _trip("b")]


@pytest.mark.asyncio
async def test_append_to_empty_creates(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.append("VIN1", _trip("a"), max_size=5)
    assert cache.get("VIN1") == [_trip("a")]


@pytest.mark.asyncio
async def test_append_max_size_zero_is_noop(hass):
    """max_size=0 means cache is disabled; append should not store anything."""
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.append("VIN1", _trip("a"), max_size=0)
    assert cache.get("VIN1") == []


@pytest.mark.asyncio
async def test_trim_shrinks_to_size(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b"), _trip("c"), _trip("d"), _trip("e")])
    cache.trim("VIN1", max_size=3)
    assert cache.get("VIN1") == [_trip("a"), _trip("b"), _trip("c")]


@pytest.mark.asyncio
async def test_trim_zero_drops_entry(hass):
    """trim(max_size=0) clears the VIN's slot entirely."""
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b")])
    cache.trim("VIN1", max_size=0)
    assert cache.get("VIN1") == []
    assert "VIN1" not in cache.known_vins()


@pytest.mark.asyncio
async def test_trim_unknown_vin_is_noop(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.trim("VIN_UNKNOWN", max_size=3)
    assert cache.get("VIN_UNKNOWN") == []


@pytest.mark.asyncio
async def test_trim_above_size_is_noop(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b")])
    cache.trim("VIN1", max_size=5)
    assert cache.get("VIN1") == [_trip("a"), _trip("b")]


@pytest.mark.asyncio
async def test_has_trip_id(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a"), _trip("b")])
    assert cache.has_trip_id("VIN1", "a") is True
    assert cache.has_trip_id("VIN1", "b") is True
    assert cache.has_trip_id("VIN1", "z") is False
    assert cache.has_trip_id("VIN_UNKNOWN", "a") is False


@pytest.mark.asyncio
async def test_clear_drops_vin(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.set("VIN1", [_trip("a")])
    cache.set("VIN2", [_trip("b")])
    cache.clear("VIN1")
    assert cache.get("VIN1") == []
    assert cache.get("VIN2") == [_trip("b")]
    assert cache.known_vins() == ["VIN2"]


@pytest.mark.asyncio
async def test_clear_unknown_vin_is_noop(hass):
    cache = TripsCacheStore(hass, "entry1")
    await cache.load()
    cache.clear("VIN_UNKNOWN")
    # no exception raised
