"""Tests for the trunk binary sensors (ha_toyota#87 capability gating).

The trunk lock/open sensors used to be gated on ``bonnet_status`` -- the
*hood's* capability flag -- so they appeared on cars that never report a
trunk and were hidden on cars that do. They are now created unconditionally,
because Toyota publishes no trunk *status* capability flag at all.

That makes their null path load-bearing: on a car with no trunk telemetry
these two entities now always exist, so they must read "unknown" rather than
a false "open"/"unlocked". The payload shapes below are taken from the
``lock_status`` JSON in ha_toyota#87.
"""

from __future__ import annotations

from custom_components.toyota.binary_sensor import (
    TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION,
    TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION,
)


class _FakeVehicle:
    """Minimal vehicle stub; lock_status is set per fixture."""

    lock_status = None


class _FakeTrunk:
    def __init__(self, closed: bool | None, locked: bool | None) -> None:
        self.closed = closed
        self.locked = locked


class _FakeDoors:
    def __init__(self, trunk: _FakeTrunk | None) -> None:
        self.trunk = trunk


class _FakeLockStatus:
    def __init__(self, doors: _FakeDoors | None) -> None:
        self.doors = doors


def _vehicle(lock_status: _FakeLockStatus | None) -> _FakeVehicle:
    vehicle = _FakeVehicle()
    vehicle.lock_status = lock_status
    return vehicle


def test_trunk_reads_unknown_on_cold_cache():
    """vehicle.lock_status is None before the first successful fetch."""
    vehicle = _vehicle(None)
    assert TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION.value_fn(vehicle) is None
    assert TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION.value_fn(vehicle) is None


def test_trunk_reads_unknown_when_doors_absent():
    """A status payload with no doors object at all."""
    vehicle = _vehicle(_FakeLockStatus(doors=None))
    assert TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION.value_fn(vehicle) is None
    assert TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION.value_fn(vehicle) is None


def test_trunk_reads_unknown_when_car_has_no_trunk_entry():
    """Doors reported, but this car never reports a trunk."""
    vehicle = _vehicle(_FakeLockStatus(doors=_FakeDoors(trunk=None)))
    assert TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION.value_fn(vehicle) is None
    assert TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION.value_fn(vehicle) is None


def test_trunk_reads_unknown_on_null_fields():
    """The ha_toyota#87 shape: trunk present, both fields null."""
    vehicle = _vehicle(
        _FakeLockStatus(doors=_FakeDoors(trunk=_FakeTrunk(closed=None, locked=None)))
    )
    assert TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION.value_fn(vehicle) is None
    assert TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION.value_fn(vehicle) is None


def test_trunk_inverts_populated_values():
    """Toyota reports closed/locked; HA DOOR and LOCK classes want the inverse."""
    vehicle = _vehicle(
        _FakeLockStatus(doors=_FakeDoors(trunk=_FakeTrunk(closed=True, locked=True)))
    )
    assert TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION.value_fn(vehicle) is False
    assert TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION.value_fn(vehicle) is False

    vehicle = _vehicle(
        _FakeLockStatus(doors=_FakeDoors(trunk=_FakeTrunk(closed=False, locked=False)))
    )
    assert TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION.value_fn(vehicle) is True
    assert TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION.value_fn(vehicle) is True


def test_trunk_fields_are_independent():
    """An open but still-unlocked-unknown trunk must not leak across fields."""
    vehicle = _vehicle(
        _FakeLockStatus(doors=_FakeDoors(trunk=_FakeTrunk(closed=False, locked=None)))
    )
    assert TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION.value_fn(vehicle) is True
    assert TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION.value_fn(vehicle) is None
