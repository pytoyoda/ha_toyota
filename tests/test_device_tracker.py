"""Tests for the Toyota parking-location device tracker."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.device_tracker import SourceType
from homeassistant.core import State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.device_tracker import (
    PARKING_TRACKER_DESCRIPTION,
    ToyotaParkingTracker,
    async_setup_entry,
)
from custom_components.toyota.entity import ToyotaBaseEntity


class _Vehicle:
    """Minimal vehicle matching the tracker's capability and location reads."""

    def __init__(
        self,
        *,
        extended: bool = False,
        features: bool = False,
        location: object | None = None,
        image: str | None = "https://example.com/car.png",
    ) -> None:
        self.vin = "JTDBR32E720000001"
        self.alias = "Test car"
        self.location = location
        self._vehicle_info = SimpleNamespace(
            brand="T",
            car_model_name="Test",
            image=image,
            extended_capabilities=SimpleNamespace(last_parked_capable=extended),
            features=SimpleNamespace(last_parked=features),
        )


def _coordinator(hass, vehicles: list[_Vehicle]):
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [
        {"data": vehicle, "statistics": None, "metric_values": True}
        for vehicle in vehicles
    ]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


@pytest.mark.asyncio
async def test_setup_skips_vehicle_without_capability(hass) -> None:
    """A vehicle reporting no last-parked capability must not get a tracker."""
    entry, _coord = _coordinator(hass, [_Vehicle()])
    entities: list[ToyotaParkingTracker] = []

    await async_setup_entry(hass, entry, entities.extend)

    assert entities == []


@pytest.mark.asyncio
async def test_setup_adds_extended_capable_vehicle(hass) -> None:
    """The extended-capabilities flag alone must add a tracker."""
    entry, _coord = _coordinator(hass, [_Vehicle(extended=True)])
    entities: list[ToyotaParkingTracker] = []

    await async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == 1
    assert entities[0].entity_description == PARKING_TRACKER_DESCRIPTION


@pytest.mark.asyncio
async def test_setup_adds_legacy_features_capable_vehicle(hass) -> None:
    """The legacy features.last_parked flag alone must add a tracker."""
    entry, _coord = _coordinator(hass, [_Vehicle(features=True)])
    entities: list[ToyotaParkingTracker] = []

    await async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == 1


def _tracker(hass, vehicle: _Vehicle) -> ToyotaParkingTracker:
    _entry, coordinator = _coordinator(hass, [vehicle])
    tracker = ToyotaParkingTracker(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=PARKING_TRACKER_DESCRIPTION,
    )
    tracker.hass = hass
    return tracker


def test_source_type_is_gps(hass) -> None:
    """The tracker must always report GPS as its source type."""
    tracker = _tracker(hass, _Vehicle(extended=True))
    assert tracker.source_type == SourceType.GPS


def test_lat_long_reflect_the_vehicle_location(hass) -> None:
    """A present location must surface latitude/longitude."""
    location = SimpleNamespace(latitude=52.5, longitude=13.4)
    tracker = _tracker(hass, _Vehicle(extended=True, location=location))
    assert tracker.latitude == 52.5
    assert tracker.longitude == 13.4


def test_lat_long_are_none_without_a_location(hass) -> None:
    """A vehicle with no location data must not crash and read as unknown."""
    tracker = _tracker(hass, _Vehicle(extended=True, location=None))
    assert tracker.latitude is None
    assert tracker.longitude is None


def test_entity_picture_reflects_vehicle_image(hass) -> None:
    """The tracker's picture must mirror the vehicle info image URL."""
    tracker = _tracker(
        hass, _Vehicle(extended=True, image="https://example.com/mycar.png")
    )
    assert tracker.entity_picture == "https://example.com/mycar.png"


def test_entity_picture_is_none_without_an_image(hass) -> None:
    """A vehicle without an image URL must not crash and read as unknown."""
    tracker = _tracker(hass, _Vehicle(extended=True, image=None))
    assert tracker.entity_picture is None


def test_keeps_last_fix_when_location_drops_out(hass) -> None:
    """A cycle without a parking fix (no coverage, driving) keeps the old one."""
    acquired = datetime(2026, 11, 23, 18, 30, tzinfo=UTC)
    vehicle = _Vehicle(
        extended=True,
        location=SimpleNamespace(latitude=37.38, longitude=-5.98, timestamp=acquired),
    )
    tracker = _tracker(hass, vehicle)
    assert (tracker.latitude, tracker.longitude) == (37.38, -5.98)

    vehicle.location = None
    assert (tracker.latitude, tracker.longitude) == (37.38, -5.98)
    assert tracker.extra_state_attributes == {
        "location_timestamp": "2026-11-23T18:30:00+00:00"
    }

    vehicle.location = SimpleNamespace(latitude=None, longitude=None, timestamp=None)
    assert (tracker.latitude, tracker.longitude) == (37.38, -5.98)


def test_new_fix_replaces_the_kept_one(hass) -> None:
    """Once the API reports a position again, it wins over the kept fix."""
    vehicle = _Vehicle(
        extended=True, location=SimpleNamespace(latitude=1.0, longitude=2.0)
    )
    tracker = _tracker(hass, vehicle)
    assert tracker.latitude == 1.0
    vehicle.location = None
    assert tracker.latitude == 1.0
    vehicle.location = SimpleNamespace(latitude=3.0, longitude=4.0)
    assert (tracker.latitude, tracker.longitude) == (3.0, 4.0)


def test_no_timestamp_attribute_without_one(hass) -> None:
    """A fix without an acquisition time adds no attribute."""
    tracker = _tracker(
        hass,
        _Vehicle(extended=True, location=SimpleNamespace(latitude=1.0, longitude=2.0)),
    )
    assert tracker.extra_state_attributes == {}


@pytest.mark.asyncio
async def test_restores_last_fix_after_restart(hass) -> None:
    """After a restart with no fix in the first payload, the saved one is used."""
    tracker = _tracker(hass, _Vehicle(extended=True, location=None))
    tracker.entity_id = "device_tracker.test_car_parking_location"
    mock_restore_cache(
        hass,
        [
            State(
                tracker.entity_id,
                "not_home",
                {
                    "latitude": 37.38,
                    "longitude": -5.98,
                    "location_timestamp": "2026-11-23T18:30:00+00:00",
                },
            )
        ],
    )

    with patch.object(ToyotaBaseEntity, "async_added_to_hass", AsyncMock()):
        await tracker.async_added_to_hass()

    assert (tracker.latitude, tracker.longitude) == (37.38, -5.98)
    assert tracker.extra_state_attributes == {
        "location_timestamp": "2026-11-23T18:30:00+00:00"
    }


@pytest.mark.asyncio
async def test_restore_ignores_state_without_coordinates(hass) -> None:
    """A saved state that was already unknown restores nothing."""
    tracker = _tracker(hass, _Vehicle(extended=True, location=None))
    tracker.entity_id = "device_tracker.test_car_parking_location"
    mock_restore_cache(hass, [State(tracker.entity_id, "unknown", {})])

    with patch.object(ToyotaBaseEntity, "async_added_to_hass", AsyncMock()):
        await tracker.async_added_to_hass()

    assert tracker.latitude is None
    assert tracker.longitude is None
