"""Regression test: cabin_temperature must be capability-gated.

pytoyoda only ever populates ``Vehicle.climate_status`` for climate-capable
vehicles (see ``Vehicle._climate_capable()``); on any other vehicle the
endpoint is never fetched and ``current_temperature`` stays permanently
``None``. Before this fix, ``sensor.py``'s extra-sensor loop created every
key in ``sensor_extra._CLASSES`` unconditionally, so cabin_temperature was
created - and stuck at unknown - for every vehicle regardless of capability.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
from pytoyoda.models.vehicle import Vehicle

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.sensor import async_setup_entry


def _vehicle_guid_payload(*, climate_capable: bool) -> dict[str, Any]:
    """Return a minimal raw `/v2/vehicle/guid` payload."""
    return {
        "alerts": [],
        "brand": "T",
        "capabilities": [],
        "evVehicle": False,
        "extendedCapabilities": {
            "climateCapable": climate_capable,
            "econnectVehicleStatusCapable": False,
            "telemetryCapable": True,
            "vehicleStatus": True,
        },
        "features": {
            "telemetry": True,
            "vehicleStatus": True,
        },
        "fuelType": "B",
        "nickName": "Test Toyota",
        "services": [],
        "subscriptions": [],
        "vin": "SB1TESTVIN00000002",
    }


def _vehicle(*, climate_capable: bool) -> Vehicle:
    return Vehicle(
        Mock(),
        VehicleGuidModel.model_validate(
            _vehicle_guid_payload(climate_capable=climate_capable)
        ),
        metric=True,
    )


async def _setup_sensors(hass, vehicle: Vehicle) -> list:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [{"data": vehicle, "statistics": None, "metric_values": True}]
    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    entities: list = []
    await async_setup_entry(hass, entry, entities.extend)
    return entities


@pytest.mark.asyncio
async def test_cabin_temperature_not_created_for_non_climate_capable_vehicle(
    hass,
) -> None:
    """No climate capability -> no cabin_temperature entity at all."""
    entities = await _setup_sensors(hass, _vehicle(climate_capable=False))
    keys = [e.entity_description.key for e in entities]
    assert "cabin_temperature" not in keys


@pytest.mark.asyncio
async def test_cabin_temperature_created_for_climate_capable_vehicle(hass) -> None:
    """Climate-capable vehicles still get the cabin_temperature entity."""
    entities = await _setup_sensors(hass, _vehicle(climate_capable=True))
    keys = [e.entity_description.key for e in entities]
    assert "cabin_temperature" in keys
