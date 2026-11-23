"""Tests for the trunk (rear hatch) lock."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.endpoints.command import CommandType

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.lock import (
    _TRUNK_ATTRIBUTES,
    TRUNK_LOCK_DESCRIPTION,
    ToyotaDoorLock,
    ToyotaTrunkLock,
    _door_lock_state,
    _trunk_lock_capable,
    async_setup_entry,
)

from .test_lock import _Vehicle


def _trunk_vehicle(*, trunk: bool = True, doors: bool = False) -> _Vehicle:
    """Vehicle that advertises (or not) remote trunk and door locking."""
    vehicle = _Vehicle(capabilities={"extended": doors})
    vehicle._vehicle_info.extended_capabilities.trunk_lock_unlock_capable = trunk
    return vehicle


def _coordinator(hass, vehicles: list[_Vehicle]) -> DataUpdateCoordinator:
    """Coordinator holding *vehicles*, registered like the integration does."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [
        {
            "data": vehicle,
            "statistics": None,
            "metric_values": True,
            "last_successful_fetch": datetime.now(UTC),
            "is_cached": False,
        }
        for vehicle in vehicles
    ]
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    return coordinator


def test_only_the_explicit_flag_enables_the_trunk_lock() -> None:
    """trunk_lock_unlock_capable must be explicitly true."""
    assert _trunk_lock_capable(_trunk_vehicle(trunk=True)) is True
    assert _trunk_lock_capable(_trunk_vehicle(trunk=False)) is False
    # door capability alone does not imply a trunk command
    assert _trunk_lock_capable(_Vehicle(capabilities={"extended": True})) is False
    info = SimpleNamespace(extended_capabilities=None)
    assert _trunk_lock_capable(SimpleNamespace(_vehicle_info=info)) is False


def test_trunk_state_reads_rear_back_only() -> None:
    """The trunk state comes from rearBack and leaves the door aggregate alone."""
    vehicle = _trunk_vehicle()
    now = datetime.now(UTC)
    vehicle.set_status(now, {"driver": "unlocked", "rearBack": "locked"})
    assert _door_lock_state(vehicle, _TRUNK_ATTRIBUTES) is True
    # the door aggregate is unaffected by the trunk
    assert _door_lock_state(vehicle) is False

    vehicle.set_status(now, {"driver": "locked", "rearBack": "unlocked"})
    assert _door_lock_state(vehicle, _TRUNK_ATTRIBUTES) is False

    vehicle.set_status(now, {"driver": "locked"})
    assert _door_lock_state(vehicle, _TRUNK_ATTRIBUTES) is None


@pytest.mark.asyncio
async def test_setup_adds_trunk_and_door_locks_by_capability(hass) -> None:
    """Each lock is created only for vehicles advertising its capability."""
    _coordinator(
        hass,
        [
            _trunk_vehicle(trunk=True, doors=True),
            _trunk_vehicle(trunk=False, doors=True),
            _trunk_vehicle(trunk=True, doors=False),
        ],
    )
    entities: list[ToyotaDoorLock] = []
    entry = hass.config_entries.async_get_entry("entry-id")

    await async_setup_entry(hass, entry, entities.extend)

    assert [type(e).__name__ for e in entities] == [
        "ToyotaDoorLock",
        "ToyotaTrunkLock",
        "ToyotaDoorLock",
        "ToyotaTrunkLock",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "command", "locked"),
    [
        ("async_lock", CommandType.TRUNK_LOCK, True),
        ("async_unlock", CommandType.TRUNK_UNLOCK, False),
    ],
)
async def test_trunk_commands(
    hass, method: str, command: CommandType, locked: bool
) -> None:
    """Lock/unlock send the trunk commands and hold the state until telemetry."""
    vehicle = _trunk_vehicle()
    coordinator = _coordinator(hass, [vehicle])
    entity = ToyotaTrunkLock(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=TRUNK_LOCK_DESCRIPTION,
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    entity._async_request_status_refresh = AsyncMock()

    await getattr(entity, method)()

    vehicle.post_command.assert_awaited_once_with(command)
    assert entity.is_locked is locked
    assert entity.assumed_state is True
    entity._async_request_status_refresh.assert_awaited_once()
    entity._clear_assumption()
