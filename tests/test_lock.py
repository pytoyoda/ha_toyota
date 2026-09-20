"""Tests for Toyota's remote door lock platform."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytoyoda.models.endpoints.common import StatusModel
from pytoyoda.models.endpoints.status import RemoteStatusResponseModel
from pytoyoda.models.lock_status import LockStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.lock import (
    DOOR_LOCK_DESCRIPTION,
    ToyotaDoorLock,
    _command_failure_reason,
    _door_lock_state,
    _lock_capable,
    async_setup_entry,
)


def _status(
    timestamp: datetime, doors: dict[str, str | None]
) -> RemoteStatusResponseModel:
    """Build a pytoyoda status model using the real endpoint payload shape."""
    payload_doors = {
        name: {"lockStatus": {"status": state}} if state is not None else {}
        for name, state in doors.items()
    }
    return RemoteStatusResponseModel.model_validate(
        {
            "payload": {
                "lastUpdateTimestamp": timestamp.isoformat(),
                "doors": payload_doors,
            }
        }
    )


class _Vehicle:
    """Minimal vehicle shaped like pytoyoda's public and status interfaces."""

    def __init__(self, *, capabilities: dict[str, bool] | None = None) -> None:
        capabilities = capabilities or {}
        self.vin = "JTDBR32E720000001"
        self.alias = "Test car"
        self._vehicle_info = SimpleNamespace(
            brand="T",
            car_model_name="Test",
            extended_capabilities=SimpleNamespace(
                door_lock_unlock_capable=capabilities.get("extended", False)
            ),
            remote_service_capabilities=SimpleNamespace(
                dlock_unlock_capable=capabilities.get("remote", False)
            ),
            features=SimpleNamespace(
                door_lock_capable=capabilities.get("features", False)
            ),
        )
        self._endpoint_data: dict[str, object] = {}
        self.post_command = AsyncMock(return_value=StatusModel())

    @property
    def lock_status(self) -> LockStatus:
        """Return pytoyoda's public lock wrapper for the status fixture."""
        return LockStatus(self._endpoint_data.get("status"))

    def set_status(self, timestamp: datetime, doors: dict[str, str | None]) -> None:
        """Replace the reported status payload."""
        self._endpoint_data["status"] = _status(timestamp, doors)


@pytest.mark.parametrize("flag", ["extended", "remote", "features"])
def test_explicit_capability_flag_creates_lock(flag: str) -> None:
    """Each known positive capability flag enables the lock platform."""
    assert _lock_capable(_Vehicle(capabilities={flag: True})) is True


def test_missing_or_false_capabilities_do_not_create_lock() -> None:
    """A status payload alone must not create a control that may always fail."""
    assert _lock_capable(_Vehicle()) is False


def test_capable_vehicle_without_status_starts_unknown(hass) -> None:
    """A cold status cache must not make a capable control disappear."""
    entity = _entity(hass, _Vehicle(capabilities={"extended": True}))
    assert entity.is_locked is None


@pytest.mark.asyncio
async def test_setup_adds_only_explicitly_capable_vehicles(hass) -> None:
    """A cold cache does not hide a capable entity or expose an incapable one."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(
        hass,
        Mock(),
        config_entry=entry,
        name="test",
    )
    coordinator.data = [
        {
            "data": _Vehicle(capabilities={"features": True}),
            "statistics": None,
            "metric_values": True,
        },
        {
            "data": _Vehicle(),
            "statistics": None,
            "metric_values": True,
        },
    ]
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    entities: list[ToyotaDoorLock] = []

    await async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == 1
    assert entities[0].is_locked is None


def test_aggregate_state_ignores_absent_doors_but_not_unknown_doors() -> None:
    """The real pytoyoda payload distinguishes absent and stateless doors."""
    vehicle = _Vehicle()
    now = datetime.now(UTC)
    vehicle.set_status(now, {"driver": "locked", "passenger": "locked"})
    assert _door_lock_state(vehicle) is True

    vehicle.set_status(now, {"driver": "locked", "passenger": None})
    assert _door_lock_state(vehicle) is None


def test_one_reported_unlocked_door_means_vehicle_is_unlocked() -> None:
    """An unlocked passenger door cannot be masked by a locked driver door."""
    vehicle = _Vehicle()
    vehicle.set_status(datetime.now(UTC), {"driver": "locked", "passenger": "unlocked"})
    assert _door_lock_state(vehicle) is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"code": 403}, "Toyota returned HTTP 403"),
        ({"errors": ["denied"]}, "Toyota reported an error"),
        ({"status": "failed"}, "Toyota rejected the command"),
        (
            {
                "status": {
                    "messages": [{"responseCode": "APIGW-403"}],
                }
            },
            "Toyota rejected the command",
        ),
        ({"code": 202}, None),
    ],
)
def test_command_failure_reason_handles_real_status_shapes(
    payload: dict[str, object], expected: str | None
) -> None:
    """Only explicit negative responses turn an accepted command into an error."""
    assert _command_failure_reason(StatusModel.model_validate(payload)) == expected


def _entity(hass, vehicle: _Vehicle) -> ToyotaDoorLock:
    """Build an entity with Home Assistant's real coordinator base class."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(
        hass,
        Mock(),
        config_entry=entry,
        name="test",
    )
    coordinator.data = [
        {
            "data": vehicle,
            "statistics": None,
            "metric_values": True,
            "last_successful_fetch": datetime.now(UTC),
            "is_cached": False,
        }
    ]
    entity = ToyotaDoorLock(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=DOOR_LOCK_DESCRIPTION,
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    entity._async_request_status_refresh = AsyncMock()
    return entity


@pytest.mark.asyncio
async def test_rejected_command_is_visible_to_the_service_caller(hass) -> None:
    """A gateway rejection must not look like a successful lock action."""
    vehicle = _Vehicle(capabilities={"extended": True})
    vehicle.post_command.return_value = StatusModel(code=403)
    entity = _entity(hass, vehicle)

    with pytest.raises(HomeAssistantError, match="HTTP 403"):
        await entity.async_lock()

    entity._async_request_status_refresh.assert_not_awaited()
    assert entity._attr_is_locking is False
    assert entity._attr_is_unlocking is False


@pytest.mark.asyncio
async def test_optimistic_state_requires_a_new_lock_timestamp(hass) -> None:
    """A fresh general coordinator cycle cannot falsely confirm a command."""
    vehicle = _Vehicle(capabilities={"extended": True})
    before = datetime.now(UTC) - timedelta(minutes=1)
    vehicle.set_status(before, {"driver": "unlocked", "passenger": "unlocked"})
    entity = _entity(hass, vehicle)

    await entity.async_lock()
    assert entity.assumed_state is True
    assert entity.is_locked is True

    entity._handle_coordinator_update()
    assert entity.assumed_state is True

    vehicle.set_status(
        datetime.now(UTC) + timedelta(seconds=1),
        {"driver": "locked", "passenger": "locked"},
    )
    entity._handle_coordinator_update()
    assert entity.assumed_state is False
    assert entity.is_locked is True


@pytest.mark.asyncio
async def test_new_conflicting_lock_telemetry_wins_over_assumption(hass) -> None:
    """A car-reported contradiction is never hidden by the optimistic window."""
    vehicle = _Vehicle(capabilities={"extended": True})
    before = datetime.now(UTC) - timedelta(minutes=1)
    vehicle.set_status(before, {"driver": "unlocked"})
    entity = _entity(hass, vehicle)

    await entity.async_lock()
    vehicle.set_status(datetime.now(UTC) + timedelta(seconds=1), {"driver": "unlocked"})
    entity._handle_coordinator_update()

    assert entity.assumed_state is False
    assert entity.is_locked is False


@pytest.mark.asyncio
async def test_second_command_invalidates_the_first_expiry_callback(hass) -> None:
    """A stale expiry callback must not clear a newer command's state."""
    vehicle = _Vehicle(capabilities={"extended": True})
    entity = _entity(hass, vehicle)
    callbacks: list[object] = []

    def schedule(_hass, _seconds, callback):
        callbacks.append(callback)
        return Mock()

    with patch("custom_components.toyota.lock.async_call_later", side_effect=schedule):
        await entity.async_lock()
        await entity.async_unlock()

    callbacks[0](datetime.now(UTC))
    assert entity.assumed_state is True
    assert entity.is_locked is False


@pytest.mark.asyncio
async def test_expired_assumption_is_unknown_until_new_telemetry_arrives(hass) -> None:
    """An old lock payload cannot be presented as the command's result."""
    vehicle = _Vehicle(capabilities={"extended": True})
    old_status = datetime.now(UTC) - timedelta(minutes=1)
    vehicle.set_status(old_status, {"driver": "unlocked"})
    entity = _entity(hass, vehicle)

    await entity.async_lock()
    entity._expire_assumption(datetime.now(UTC), entity._command_generation)

    assert entity.assumed_state is False
    assert entity.is_locked is None

    vehicle.set_status(datetime.now(UTC) + timedelta(seconds=1), {"driver": "unlocked"})
    entity._handle_coordinator_update()
    assert entity.is_locked is False


@pytest.mark.asyncio
async def test_lock_uses_existing_vehicle_wake_service(hass) -> None:
    """An accepted command requests the established per-device status wake."""
    vehicle = _Vehicle(capabilities={"extended": True})
    entity = _entity(hass, vehicle)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id",
        identifiers={(DOMAIN, vehicle.vin)},
    )
    calls: list[dict[str, object]] = []

    async def handle_refresh(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_vehicle_status", handle_refresh)
    entity._async_request_status_refresh = (
        ToyotaDoorLock._async_request_status_refresh.__get__(entity)
    )

    await entity.async_lock()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id]}]
    entity._clear_assumption()
