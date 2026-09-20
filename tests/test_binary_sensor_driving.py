"""Tests for the driving/parked status binary sensor (ha_toyota#18).

Toyota's API does not expose a live ignition/GPS "driving" signal (see
``__init__.py``'s odometer-delta strategy and ``get_location`` docstring in
pytoyoda, which only updates when the car is parked). This sensor infers
movement from whether the odometer advanced between the last two poll
cycles, a value the coordinator's refresh loop persists per-VIN into
``was_moving_last_cycle_per_vin`` / ``last_odometer_km_per_vin`` and exposes
on the coordinator as ``_diag_was_moving_last_cycle_per_vin`` /
``_diag_last_odometer_km_per_vin`` (the same pattern used by the existing
``status_refresh_state`` diagnostic sensor in sensor.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.binary_sensor import (
    DRIVING_STATUS_ENTITY_DESCRIPTION,
    ToyotaDrivingStatusBinarySensor,
)
from custom_components.toyota.const import DOMAIN


def _vehicle(vin: str = "JTDBR32E720000001") -> SimpleNamespace:
    return SimpleNamespace(
        vin=vin,
        alias="Test car",
        _vehicle_info=SimpleNamespace(brand="T", car_model_name="Test"),
    )


def _entity(
    hass,
    *,
    was_moving_per_vin: dict[str, bool] | None,
    last_odometer_per_vin: dict[str, float | None] | None,
) -> ToyotaDrivingStatusBinarySensor:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(
        hass,
        Mock(),
        config_entry=entry,
        name="test",
    )
    vehicle = _vehicle()
    coordinator.data = [
        {"data": vehicle, "statistics": None, "metric_values": True},
    ]
    if was_moving_per_vin is not None:
        coordinator._diag_was_moving_last_cycle_per_vin = was_moving_per_vin  # noqa: SLF001
    if last_odometer_per_vin is not None:
        coordinator._diag_last_odometer_km_per_vin = last_odometer_per_vin  # noqa: SLF001

    return ToyotaDrivingStatusBinarySensor(
        coordinator=coordinator,
        entry_id=entry.entry_id,
        vehicle_index=0,
        description=DRIVING_STATUS_ENTITY_DESCRIPTION,
    )


@pytest.mark.asyncio
async def test_driving_true_when_odometer_advanced(hass) -> None:
    """Odometer advanced since the previous cycle -> reported as driving/on."""
    entity = _entity(
        hass,
        was_moving_per_vin={"JTDBR32E720000001": True},
        last_odometer_per_vin={"JTDBR32E720000001": 12345.6},
    )
    assert entity.is_on is True
    assert entity.available is True


@pytest.mark.asyncio
async def test_driving_false_when_odometer_unchanged(hass) -> None:
    """Odometer unchanged since the previous cycle -> reported as parked/off."""
    entity = _entity(
        hass,
        was_moving_per_vin={"JTDBR32E720000001": False},
        last_odometer_per_vin={"JTDBR32E720000001": 12345.6},
    )
    assert entity.is_on is False


@pytest.mark.asyncio
async def test_driving_unknown_when_odometer_never_recorded(hass) -> None:
    """No odometer reading has ever been recorded for this VIN yet."""
    entity = _entity(
        hass,
        was_moving_per_vin={},
        last_odometer_per_vin={},
    )
    assert entity.is_on is None
    # Diagnostic sensor stays available even while the value is unknown.
    assert entity.available is True


@pytest.mark.asyncio
async def test_driving_unknown_when_diag_dicts_missing(hass) -> None:
    """Coordinator hasn't wired the diag attrs yet (defensive, shouldn't crash)."""
    entity = _entity(hass, was_moving_per_vin=None, last_odometer_per_vin=None)
    assert entity.is_on is None


@pytest.mark.asyncio
async def test_driving_extra_state_attributes_document_limitation(hass) -> None:
    """The entity documents that it is poll-bound, not a live signal."""
    entity = _entity(
        hass,
        was_moving_per_vin={"JTDBR32E720000001": True},
        last_odometer_per_vin={"JTDBR32E720000001": 1.0},
    )
    attrs = entity.extra_state_attributes
    assert attrs is not None
    assert "polling interval" in attrs["note"]
