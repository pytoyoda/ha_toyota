"""Tests for the Toyota refresh-status/trips/electric button entities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.button import (
    REFRESH_BUTTON_DESCRIPTION,
    REFRESH_ELECTRIC_REALTIME_STATUS_BUTTON_DESCRIPTION,
    REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION,
    ToyotaRefreshElectricRealtimeStatusButton,
    ToyotaRefreshRecentTripsButton,
    ToyotaRefreshStatusButton,
    async_setup_entry,
)
from custom_components.toyota.const import CONF_MAX_RECENT_TRIPS, DOMAIN


class _Vehicle:
    """Minimal vehicle matching the capability checks button.py relies on."""

    def __init__(self, *, type_: str = "ice", econnect: bool = False) -> None:
        self.vin = "JTDBR32E720000001"
        self.alias = "Test car"
        self.type = type_
        self._vehicle_info = SimpleNamespace(
            brand="T",
            car_model_name="Test",
            extended_capabilities=SimpleNamespace(
                econnect_vehicle_status_capable=econnect
            ),
        )


def _coordinator(hass, vehicles: list[_Vehicle]) -> tuple[MockConfigEntry, DataUpdateCoordinator]:
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
async def test_setup_adds_status_and_trips_buttons_for_every_vehicle(hass) -> None:
    """Every vehicle gets a status button and a recent-trips button."""
    entry, _coord = _coordinator(hass, [_Vehicle(), _Vehicle()])
    entities: list = []

    await async_setup_entry(hass, entry, entities.extend)

    status_buttons = [e for e in entities if isinstance(e, ToyotaRefreshStatusButton)]
    trip_buttons = [
        e for e in entities if isinstance(e, ToyotaRefreshRecentTripsButton)
    ]
    assert len(status_buttons) == 2
    assert len(trip_buttons) == 2


@pytest.mark.asyncio
async def test_setup_adds_electric_button_only_when_capable(hass) -> None:
    """The electric-realtime-status button is gated on capability or EV type."""
    entry, _coord = _coordinator(
        hass,
        [
            _Vehicle(econnect=True),
            _Vehicle(type_="electric"),
            _Vehicle(),
        ],
    )
    entities: list = []

    await async_setup_entry(hass, entry, entities.extend)

    electric_buttons = [
        e
        for e in entities
        if isinstance(e, ToyotaRefreshElectricRealtimeStatusButton)
    ]
    assert len(electric_buttons) == 2


def _button(hass, cls, vehicle: _Vehicle, description) -> object:
    _entry, coordinator = _coordinator(hass, [vehicle])
    button = cls(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=description,
    )
    button.hass = hass
    return button


@pytest.mark.asyncio
async def test_status_button_without_device_is_a_noop(hass) -> None:
    """Pressing before the device is registered must not raise."""
    button = _button(hass, ToyotaRefreshStatusButton, _Vehicle(), REFRESH_BUTTON_DESCRIPTION)
    await button.async_press()  # must not raise


@pytest.mark.asyncio
async def test_status_button_calls_refresh_service_with_device_id(hass) -> None:
    """Pressing calls toyota.refresh_vehicle_status with the right device id."""
    vehicle = _Vehicle()
    button = _button(hass, ToyotaRefreshStatusButton, vehicle, REFRESH_BUTTON_DESCRIPTION)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id", identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_vehicle_status", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id]}]


@pytest.mark.asyncio
async def test_recent_trips_button_uses_configured_max_trips(hass) -> None:
    """The limit sent must be the configured max_recent_trips option."""
    vehicle = _Vehicle()
    entry, coordinator = _coordinator(hass, [vehicle])
    hass.config_entries._entries[entry.entry_id] = entry
    hass.config_entries.async_update_entry(entry, options={CONF_MAX_RECENT_TRIPS: 12})
    button = ToyotaRefreshRecentTripsButton(
        coordinator=coordinator,
        entry_id=entry.entry_id,
        vehicle_index=0,
        description=REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION,
    )
    button.hass = hass
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_recent_trips", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id], "limit": 12}]


@pytest.mark.asyncio
async def test_recent_trips_button_falls_back_when_auto_fetch_disabled(hass) -> None:
    """max_recent_trips=0 (auto-fetch off) must fall back to the manual default."""
    vehicle = _Vehicle()
    entry, coordinator = _coordinator(hass, [vehicle])
    hass.config_entries.async_update_entry(entry, options={CONF_MAX_RECENT_TRIPS: 0})
    button = ToyotaRefreshRecentTripsButton(
        coordinator=coordinator,
        entry_id=entry.entry_id,
        vehicle_index=0,
        description=REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION,
    )
    button.hass = hass
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_recent_trips", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id], "limit": 5}]


@pytest.mark.asyncio
async def test_electric_button_calls_electric_refresh_service(hass) -> None:
    """Pressing the electric-status button calls the matching service."""
    vehicle = _Vehicle(econnect=True)
    button = _button(
        hass,
        ToyotaRefreshElectricRealtimeStatusButton,
        vehicle,
        REFRESH_ELECTRIC_REALTIME_STATUS_BUTTON_DESCRIPTION,
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id", identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_electric_realtime_status", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id]}]
