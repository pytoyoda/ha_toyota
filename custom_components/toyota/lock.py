"""Lock platform for Toyota Connected Services."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from pytoyoda.models.endpoints.command import CommandType

from .const import DOMAIN
from .entity import ToyotaBaseEntity
from .utils import command_failure_reason as _command_failure_reason

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
    from pytoyoda.models.vehicle import Vehicle

    from . import VehicleData

_LOGGER = logging.getLogger(__name__)

_OPTIMISTIC_SECONDS = 180
_DOOR_ATTRIBUTES = ("driver", "passenger", "rear_left", "rear_right")

DOOR_LOCK_DESCRIPTION = LockEntityDescription(
    key="door_lock",
    translation_key="door_lock",
)


def _lock_capable(vehicle: Vehicle) -> bool:
    """Return whether *vehicle* explicitly advertises remote door locking."""
    info = getattr(vehicle, "_vehicle_info", None)
    return any(
        getattr(getattr(info, block, None), flag, False) is True
        for block, flag in (
            ("extended_capabilities", "door_lock_unlock_capable"),
            ("remote_service_capabilities", "dlock_unlock_capable"),
            ("features", "door_lock_capable"),
        )
    )


def _door_lock_state(vehicle: Vehicle) -> bool | None:
    """Return the aggregate reported door lock state, or ``None`` when unknown.

    This reads the typed status payload directly so absent doors can be
    distinguished from doors which Toyota reported without a lock state.  The
    public pytoyoda wrapper represents both as ``Door(None)``.
    """
    doors = _reported_doors(vehicle)
    if doors is None:
        return None
    states = _reported_door_lock_states(doors)
    if not states:
        return None
    normalized = [state.lower() if isinstance(state, str) else None for state in states]
    if "unlocked" in normalized:
        return False
    if any(state != "locked" for state in normalized):
        return None
    return True


def _reported_doors(vehicle: Vehicle) -> object | None:
    """Return the raw typed doors payload, if pytoyoda has one."""
    endpoint_data = getattr(vehicle, "_endpoint_data", {})
    if not isinstance(endpoint_data, dict):
        return None
    status = endpoint_data.get("status")
    return getattr(getattr(status, "payload", None), "doors", None)


def _reported_door_lock_states(doors: object) -> list[str | None]:
    """Return lock states for doors that are actually present in the payload."""
    states: list[str | None] = []
    for attribute in _DOOR_ATTRIBUTES:
        door = getattr(doors, attribute, None)
        if door is not None:
            states.append(getattr(getattr(door, "lock_status", None), "status", None))
    return states


def _lock_timestamp(vehicle: Vehicle) -> datetime | None:
    """Return the timestamp attached to the currently reported lock status."""
    timestamp = getattr(getattr(vehicle, "lock_status", None), "last_updated", None)
    if timestamp is None or not isinstance(timestamp, datetime):
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota lock entities for one config entry."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]
    async_add_entities(
        ToyotaDoorLock(
            coordinator=coordinator,
            entry_id=entry.entry_id,
            vehicle_index=index,
            description=DOOR_LOCK_DESCRIPTION,
        )
        for index, vehicle_data in enumerate(coordinator.data)
        if _lock_capable(vehicle_data["data"])
    )


class ToyotaDoorLock(ToyotaBaseEntity, LockEntity):
    """Lock entity for all doors controlled by Toyota's door command."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: LockEntityDescription,
    ) -> None:
        """Initialize state that exists only while a command awaits telemetry."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self._assumed_locked: bool | None = None
        self._command_status_timestamp: datetime | None = None
        self._command_started_at: datetime | None = None
        self._requires_fresh_status = False
        self._command_generation = 0
        self._cancel_assumption: Callable[[], None] | None = None
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self.async_on_remove(self._clear_assumption)

    @property
    def assumed_state(self) -> bool:
        """Return whether the displayed state is awaiting vehicle telemetry."""
        return self._assumed_locked is not None

    @property
    def is_locked(self) -> bool | None:
        """Return optimistic state until fresh lock telemetry supersedes it."""
        if self._assumed_locked is not None:
            return self._assumed_locked
        if self._requires_fresh_status and not self._status_is_new_since_command():
            return None
        return _door_lock_state(self.vehicle)

    @property
    def icon(self) -> str:
        """Return a door-lock icon matching the current state."""
        if self.is_locked is False:
            return "mdi:car-door-lock-open"
        return "mdi:car-door-lock"

    def _clear_assumption(self, *, keep_freshness: bool = False) -> None:
        """Discard a pending optimistic state and its expiry callback."""
        self._assumed_locked = None
        if not keep_freshness:
            self._command_status_timestamp = None
            self._command_started_at = None
            self._requires_fresh_status = False
        if self._cancel_assumption is not None:
            self._cancel_assumption()
            self._cancel_assumption = None

    def _status_is_new_since_command(self) -> bool:
        """Return whether current status is demonstrably newer than the command."""
        timestamp = _lock_timestamp(self.vehicle)
        if timestamp is None:
            return False
        if self._command_status_timestamp is not None:
            return timestamp > self._command_status_timestamp
        return (
            self._command_started_at is not None
            and timestamp >= self._command_started_at
        )

    def _expire_assumption(self, _now: datetime, generation: int) -> None:
        """Stop displaying an unconfirmed command after its bounded window."""
        if generation != self._command_generation or self._assumed_locked is None:
            return
        self._clear_assumption(keep_freshness=True)
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Use only a new lock payload to retire an optimistic state."""
        super()._handle_coordinator_update()
        if self._requires_fresh_status and self._status_is_new_since_command():
            self._clear_assumption()

    async def async_lock(self, **_kwargs: object) -> None:
        """Lock the vehicle doors."""
        await self._async_send_command(CommandType.DOOR_LOCK, locked=True)

    async def async_unlock(self, **_kwargs: object) -> None:
        """Unlock the vehicle doors."""
        await self._async_send_command(CommandType.DOOR_UNLOCK, locked=False)

    async def _async_send_command(self, command: CommandType, *, locked: bool) -> None:
        """Send one door command, then wait briefly for fresh lock telemetry."""
        self._attr_is_locking = locked
        self._attr_is_unlocking = not locked
        self.async_write_ha_state()
        try:
            try:
                response = await self.vehicle.post_command(command)
            except Exception as err:
                msg = "Toyota could not send the door command"
                raise HomeAssistantError(msg) from err

            if reason := _command_failure_reason(response):
                raise HomeAssistantError(reason)

            self._command_generation += 1
            generation = self._command_generation
            self._clear_assumption()
            self._assumed_locked = locked
            self._command_status_timestamp = _lock_timestamp(self.vehicle)
            # Toyota's status timestamp is commonly second-granular. Keep the
            # same precision here so a status reported in the command second
            # is not needlessly held as unconfirmed for another full cycle.
            self._command_started_at = (
                dt_util.utcnow().astimezone(UTC).replace(microsecond=0)
            )
            self._requires_fresh_status = True
            self._cancel_assumption = async_call_later(
                self.hass,
                _OPTIMISTIC_SECONDS,
                lambda now: self._expire_assumption(now, generation),
            )
            self.async_write_ha_state()
            await self._async_request_status_refresh()
        finally:
            self._attr_is_locking = False
            self._attr_is_unlocking = False
            self.async_write_ha_state()

    async def _async_request_status_refresh(self) -> None:
        """Ask the integration's existing per-device wake service for fresh data."""
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.vehicle.vin or "")}
        )
        if device is None:
            _LOGGER.debug("No Toyota device registered for lock refresh")
            return
        await self.hass.services.async_call(
            DOMAIN,
            "refresh_vehicle_status",
            {"device_id": [device.id]},
            blocking=False,
        )
