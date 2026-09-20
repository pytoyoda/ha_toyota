"""Tests for the Toyota parking-location device tracker."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.device_tracker import SourceType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.device_tracker import (
    PARKING_TRACKER_DESCRIPTION,
    ToyotaParkingTracker,
    async_setup_entry,
)


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
