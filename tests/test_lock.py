"""Tests for the remote vehicle lock platform.

Two layers: the pure helpers that decide what the user sees and whether a
command is treated as having failed, and the entity itself driven against a
stub vehicle — setup gating, which commands each operation sends, and how the
optimistic window opens and retires.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytoyoda.models.endpoints.command import CommandType

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.lock import (
    DOOR_CAPABILITY_FLAGS,
    LOCK_ATTRIBUTES,
    LOCK_DESCRIPTION,
    OPTIMISTIC_SECONDS,
    TRUNK_CAPABILITY_FLAGS,
    ToyotaVehicleLock,
    async_setup_entry,
    command_supported,
    rejection_reason,
    vehicle_locked,
)

DOOR_ATTRIBUTES = tuple(name for name in LOCK_ATTRIBUTES if name != "trunk")


class _FakeDoor:
    """One opening with a lock state."""

    def __init__(self, locked: bool | None) -> None:
        self.locked = locked


class _FakeDoors:
    """Opening container exposing only the openings it is given.

    Mirrors pytoyoda: a model that does not report an opening simply has no
    attribute for it, which is different from reporting it with no state.
    """

    def __init__(self, **doors: _FakeDoor) -> None:
        for name, door in doors.items():
            setattr(self, name, door)


class _FakeResponse:
    """Minimal StatusModel stand-in."""

    def __init__(
        self,
        status: object = None,
        code: int | None = None,
        errors: list | None = None,
        message: str | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.errors = errors
        self.message = message


def _all(locked: bool | None) -> _FakeDoors:
    return _FakeDoors(**{name: _FakeDoor(locked) for name in LOCK_ATTRIBUTES})


# --- vehicle_locked --------------------------------------------------------


def test_no_door_container_is_unknown():
    assert vehicle_locked(None) is None


def test_empty_door_container_is_unknown():
    assert vehicle_locked(_FakeDoors()) is None


def test_every_opening_locked():
    assert vehicle_locked(_all(True)) is True


def test_every_opening_unlocked():
    assert vehicle_locked(_all(False)) is False


def test_one_unlocked_door_beats_the_rest():
    doors = _all(True)
    doors.driver_seat = _FakeDoor(False)
    assert vehicle_locked(doors) is False


def test_unlocked_trunk_beats_locked_doors():
    """The trunk counts: a car with an unlocked trunk is not secured."""
    doors = _all(True)
    doors.trunk = _FakeDoor(False)
    assert vehicle_locked(doors) is False


def test_unknown_opening_state_is_not_reported_as_locked():
    """The regression this platform's design hinges on.

    Every other opening locked and one the car has not reported must NOT read
    as "locked" — that would tell the user the car is secured when it may not
    be.
    """
    doors = _all(True)
    doors.driver_seat = _FakeDoor(None)
    assert vehicle_locked(doors) is None


def test_unknown_opening_state_does_not_mask_an_unlocked_one():
    """Unknown does not outrank an opening known to be unlocked."""
    doors = _all(True)
    doors.driver_seat = _FakeDoor(None)
    doors.passenger_seat = _FakeDoor(False)
    assert vehicle_locked(doors) is False


def test_missing_openings_are_ignored():
    """A model reporting fewer doors and no trunk still yields a state."""
    doors = _FakeDoors(driver_seat=_FakeDoor(True), passenger_seat=_FakeDoor(True))
    assert vehicle_locked(doors) is True


def test_all_openings_stateless_is_unknown():
    assert vehicle_locked(_all(None)) is None


# --- rejection_reason ------------------------------------------------------


def test_empty_response_is_accepted():
    """An unrecognised shape must not turn a working command into an error."""
    assert rejection_reason(_FakeResponse()) is None


def test_success_code_is_accepted():
    assert rejection_reason(_FakeResponse(code=200)) is None


def test_errors_field_is_a_rejection():
    reason = rejection_reason(_FakeResponse(errors=["nope"]))
    assert reason is not None
    assert "nope" in reason


def test_non_2xx_code_is_a_rejection():
    assert rejection_reason(_FakeResponse(code=503)) == "code 503"


def test_error_status_is_a_rejection():
    assert rejection_reason(_FakeResponse(status="ERROR")) == "ERROR"


def test_error_status_prefers_the_message():
    reason = rejection_reason(_FakeResponse(status="failed", message="key in car"))
    assert reason == "key in car"


def test_unknown_status_string_is_accepted():
    """Only positively-known failure words count as a rejection."""
    assert rejection_reason(_FakeResponse(status="queued")) is None


# --- command_supported -----------------------------------------------------


def _vehicle_info(**blocks: dict) -> SimpleNamespace:
    return SimpleNamespace(**{k: SimpleNamespace(**v) for k, v in blocks.items()})


def test_vehicle_reporting_no_capability_blocks_is_supported():
    """Payloads without capability blocks keep the entity they always had."""
    vehicle = SimpleNamespace(_vehicle_info=None)
    assert command_supported(vehicle, DOOR_CAPABILITY_FLAGS) is True


def test_capability_reported_true_is_supported():
    vehicle = SimpleNamespace(
        _vehicle_info=_vehicle_info(
            extended_capabilities={"door_lock_unlock_capable": True}
        )
    )
    assert command_supported(vehicle, DOOR_CAPABILITY_FLAGS) is True


def test_every_reported_capability_false_is_unsupported():
    vehicle = SimpleNamespace(
        _vehicle_info=_vehicle_info(
            extended_capabilities={"door_lock_unlock_capable": False},
            remote_service_capabilities={"dlock_unlock_capable": False},
        )
    )
    assert command_supported(vehicle, DOOR_CAPABILITY_FLAGS) is False


def test_one_reported_capability_is_enough():
    """The gateway populates these blocks inconsistently; any yes wins."""
    vehicle = SimpleNamespace(
        _vehicle_info=_vehicle_info(
            extended_capabilities={"trunk_lock_unlock_capable": False},
            remote_service_capabilities={"trunk_capable": True},
        )
    )
    assert command_supported(vehicle, TRUNK_CAPABILITY_FLAGS) is True


# --- entity ----------------------------------------------------------------


class _FakeVehicle:
    """Vehicle stub covering what ToyotaBaseEntity and the lock read."""

    def __init__(
        self,
        doors: _FakeDoors | None,
        *,
        capabilities: dict | None = None,
        response: object = None,
        error: Exception | None = None,
    ) -> None:
        self.vin = "VIN0000000000000"
        self.alias = "Test car"
        self.lock_status = SimpleNamespace(doors=doors) if doors is not None else None
        self._vehicle_info = SimpleNamespace(
            car_model_name="Yaris Cross",
            brand="T",
            **{k: SimpleNamespace(**v) for k, v in (capabilities or {}).items()},
        )
        self.commands: list[CommandType] = []
        self._response = response if response is not None else _FakeResponse()
        self._error = error

    async def post_command(self, command: CommandType) -> object:
        self.commands.append(command)
        if self._error is not None:
            raise self._error
        return self._response


class _FakeCoordinator:
    """DataUpdateCoordinator stand-in holding one refreshable payload."""

    def __init__(self, *vehicles: _FakeVehicle) -> None:
        self.data = [
            {"data": vehicle, "statistics": None, "metric_values": True}
            for vehicle in vehicles
        ]
        self.refreshes = 0

    async def async_request_refresh(self) -> None:
        self.refreshes += 1


def _entity(
    vehicle: _FakeVehicle, monkeypatch: pytest.MonkeyPatch
) -> ToyotaVehicleLock:
    """Build an entity detached from hass, with state writes recorded."""
    from custom_components.toyota import lock as lock_module

    monkeypatch.setattr(
        lock_module, "async_call_later", lambda *_args, **_kwargs: (lambda: None)
    )
    entity = ToyotaVehicleLock(
        coordinator=_FakeCoordinator(vehicle),
        entry_id="entry",
        vehicle_index=0,
        description=LOCK_DESCRIPTION,
        command_type=CommandType,
    )
    entity.hass = None
    entity.writes = 0

    def _write() -> None:
        entity.writes += 1

    entity.async_write_ha_state = _write
    return entity


async def _setup(*vehicles: _FakeVehicle) -> list[ToyotaVehicleLock]:
    """Run the platform setup against a stub hass and return the entities."""
    coordinator = _FakeCoordinator(*vehicles)
    hass = SimpleNamespace(data={DOMAIN: {"entry": coordinator}})
    entry = SimpleNamespace(entry_id="entry")
    added: list[ToyotaVehicleLock] = []
    await async_setup_entry(hass, entry, added.extend)
    return added


async def test_setup_skips_a_vehicle_without_lock_state():
    assert await _setup(_FakeVehicle(None)) == []


async def test_setup_skips_a_vehicle_that_declares_the_command_unsupported():
    vehicle = _FakeVehicle(
        _all(True),
        capabilities={"extended_capabilities": {"door_lock_unlock_capable": False}},
    )
    assert await _setup(vehicle) == []


async def test_setup_adds_one_entity_per_capable_vehicle():
    entities = await _setup(_FakeVehicle(_all(True)), _FakeVehicle(_all(False)))
    assert len(entities) == 2
    assert entities[0].unique_id == "entry_VIN0000000000000/vehicle_lock_control"


async def test_lock_sends_the_trunk_command_too(monkeypatch):
    """The trunk counts towards is_locked, so it has to be commanded."""
    vehicle = _FakeVehicle(_all(False))
    entity = _entity(vehicle, monkeypatch)

    await entity.async_lock()

    assert vehicle.commands == [CommandType.DOOR_LOCK, CommandType.TRUNK_LOCK]
    assert entity.is_locked is True
    assert entity.writes == 1


async def test_unlock_sends_the_unlock_pair(monkeypatch):
    vehicle = _FakeVehicle(_all(True))
    entity = _entity(vehicle, monkeypatch)

    await entity.async_unlock()

    assert vehicle.commands == [CommandType.DOOR_UNLOCK, CommandType.TRUNK_UNLOCK]
    assert entity.is_locked is False


async def test_a_car_without_a_trunk_only_gets_the_door_command(monkeypatch):
    doors = _FakeDoors(**{name: _FakeDoor(False) for name in DOOR_ATTRIBUTES})
    vehicle = _FakeVehicle(doors)
    entity = _entity(vehicle, monkeypatch)

    await entity.async_lock()

    assert vehicle.commands == [CommandType.DOOR_LOCK]


async def test_a_car_declaring_no_trunk_command_only_gets_the_door_command(monkeypatch):
    vehicle = _FakeVehicle(
        _all(False),
        capabilities={"extended_capabilities": {"trunk_lock_unlock_capable": False}},
    )
    entity = _entity(vehicle, monkeypatch)

    await entity.async_lock()

    assert vehicle.commands == [CommandType.DOOR_LOCK]


async def test_a_rejected_command_raises_and_leaves_the_state_alone(monkeypatch):
    vehicle = _FakeVehicle(_all(False), response=_FakeResponse(status="failed"))
    entity = _entity(vehicle, monkeypatch)

    with pytest.raises(HomeAssistantError):
        await entity.async_lock()

    assert entity.is_locked is False  # still what the car reports
    assert entity.writes == 0


async def test_a_transport_failure_raises(monkeypatch):
    vehicle = _FakeVehicle(_all(False), error=TimeoutError("timed out"))
    entity = _entity(vehicle, monkeypatch)

    with pytest.raises(HomeAssistantError):
        await entity.async_lock()

    assert entity.is_locked is False


async def test_the_optimistic_state_lapses_back_to_what_the_car_reports(monkeypatch):
    vehicle = _FakeVehicle(_all(False))
    entity = _entity(vehicle, monkeypatch)

    await entity.async_lock()
    assert entity.is_locked is True

    monkeypatch.setattr(
        time, "monotonic", lambda: entity._optimistic_until + OPTIMISTIC_SECONDS
    )
    assert entity.is_locked is False


async def test_a_confirming_refresh_retires_the_optimistic_state(monkeypatch):
    vehicle = _FakeVehicle(_all(False))
    entity = _entity(vehicle, monkeypatch)
    await entity.async_lock()

    # The car now reports what was commanded.
    vehicle.lock_status = SimpleNamespace(doors=_all(True))
    entity._handle_coordinator_update()

    assert entity._optimistic_locked is None
    assert entity.is_locked is True


async def test_a_refresh_that_does_not_confirm_keeps_the_window_open(monkeypatch):
    vehicle = _FakeVehicle(_all(False))
    entity = _entity(vehicle, monkeypatch)
    await entity.async_lock()

    entity._handle_coordinator_update()

    assert entity._optimistic_locked is True
    assert entity.is_locked is True
