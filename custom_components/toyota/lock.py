"""Remote lock for Toyota Connected Services.

The integration already reads lock state through binary_sensor but never
writes it, even though pytoyoda exposes ``Vehicle.post_command`` with
``DOOR_LOCK`` / ``DOOR_UNLOCK`` as of v5.2.0 (the minimum this integration
requires). This platform adds the write path.

One entity per vehicle rather than one per opening: ``DOOR_LOCK`` has no
per-door variant, so four door controls would all do the same thing. The
trunk has its own command pair but is folded into the same entity — it is
part of whether the car is secured, not a separate thing to reason about —
so operating the lock sends the trunk command too whenever the car reports a
trunk. ``binary_sensor`` still reports every door and the trunk individually
for anyone who wants the detail.
"""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, ICON_CAR_DOOR_LOCK
from .entity import ToyotaBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import VehicleData

_LOGGER = logging.getLogger(__name__)

# Openings that make up the vehicle's lock state, in pytoyoda's naming. The
# trunk is included: an unlocked trunk means the car is not secured, and
# central locking operates it along with the doors.
LOCK_ATTRIBUTES = (
    "driver_seat",
    "passenger_seat",
    "driver_rear_seat",
    "passenger_rear_seat",
    "trunk",
)

# How long a just-sent command is trusted over the reported state.
#
# Remote commands are fire-and-forget: the gateway accepts the command and
# the car reports its new state whenever its modem next transmits, which can
# take minutes. Without this window the control would snap back to the old
# state immediately after being operated and read as a failure. Once it
# expires the reported state wins again, so a command the car silently
# dropped does surface as not applied.
OPTIMISTIC_SECONDS = 180

# Delay before requesting fresh coordinator data after a command. This is a
# cache read and does NOT wake the modem; the refresh_vehicle_status service
# (or its button) is what forces a live read.
REFRESH_DELAY_SECONDS = 20

# Status strings that positively indicate the gateway rejected a command.
_REJECTED_STATUSES = frozenset({"error", "failed", "failure", "rejected"})

# Capability flags that say a vehicle accepts the remote lock commands. The
# gateway populates these blocks inconsistently across models and regions, so
# each command is described by every flag that can answer for it.
DOOR_CAPABILITY_FLAGS = (
    ("extended_capabilities", "door_lock_unlock_capable"),
    ("remote_service_capabilities", "dlock_unlock_capable"),
    ("features", "door_lock_capable"),
)
TRUNK_CAPABILITY_FLAGS = (
    ("extended_capabilities", "trunk_lock_unlock_capable"),
    ("remote_service_capabilities", "trunk_capable"),
)

LOCK_DESCRIPTION = LockEntityDescription(
    # Not "driverseat_lock"/"trunk_lock": those keys belong to the
    # binary_sensor entities and unique_id is f"{entry_id}_{vin}/{key}".
    key="vehicle_lock_control",
    translation_key="vehicle_lock_control",
    icon=ICON_CAR_DOOR_LOCK,
)


def vehicle_locked(doors: object) -> bool | None:
    """Return whether the vehicle is locked.

    Distinguishes an opening the vehicle does not report at all (attribute
    missing — not every model exposes four doors or a trunk) from one it
    reports without a state (``locked is None``, typically a cold cache):

    * missing openings are ignored, so a model reporting fewer of them still
      yields a usable state;
    * one present but stateless forces ``None`` unless another is already
      known to be unlocked.

    The asymmetry is deliberate. ``False`` is safe to report early — one
    unlocked door means the car is not secured regardless of the rest. But
    ``True`` is only ever returned when *every* reported opening confirms it,
    because for a lock entity "not sure" must never render as "secured".
    """
    if doors is None:
        return None
    present = [
        door
        for door in (getattr(doors, name, None) for name in LOCK_ATTRIBUTES)
        if door is not None
    ]
    if not present:
        return None
    states = [getattr(door, "locked", None) for door in present]
    if any(state is False for state in states):
        return False
    if any(state is None for state in states):
        return None
    return True


def command_supported(vehicle: object, flags: tuple[tuple[str, str], ...]) -> bool:
    """Return whether a vehicle accepts the commands described by ``flags``.

    Only an explicit "no" keeps the control away: a vehicle is treated as
    capable unless every flag it actually reports says otherwise. Cars that
    report none of these blocks — common on older payloads — keep the entity
    they would have had before capability gating existed, and a car that
    accepts the entity but rejects the command still surfaces the rejection
    when the control is operated.
    """
    info = getattr(vehicle, "_vehicle_info", None)
    reported = [
        value
        for value in (
            getattr(getattr(info, block, None), flag, None) for block, flag in flags
        )
        if value is not None
    ]
    return any(reported) if reported else True


def rejection_reason(response: object) -> str | None:
    """Return why a command was rejected, or ``None`` if it was accepted.

    ``StatusModel`` carries ``status`` / ``code`` / ``errors`` / ``message``
    and the gateway is not consistent about which it populates. Treat a
    response as a rejection only on positive evidence of one, so an
    unrecognised shape does not turn a command that worked into an error.
    The optimistic window lapsing on unchanged state is the backstop for a
    command that was silently dropped.
    """
    errors = getattr(response, "errors", None)
    if errors:
        return str(errors)
    code = getattr(response, "code", None)
    if (
        isinstance(code, int)
        and not HTTPStatus.OK <= code < HTTPStatus.MULTIPLE_CHOICES
    ):
        return f"code {code}"
    status = getattr(response, "status", None)
    if isinstance(status, str) and status.lower() in _REJECTED_STATUSES:
        return getattr(response, "message", None) or status
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Toyota lock platform."""
    # Imported here rather than at module scope on purpose. A pytoyoda
    # release that renames or drops a symbol imported at module scope takes
    # the whole config entry down with an ImportError during platform
    # forwarding, including entities unrelated to it. Scoping the import
    # keeps such a break contained to this platform.
    try:
        from pytoyoda.models.endpoints.command import (  # noqa: PLC0415
            CommandType,
        )
    except ImportError:
        _LOGGER.warning(
            "Installed pytoyoda does not expose CommandType; remote lock and "
            "unlock are unavailable. The rest of the integration is unaffected."
        )
        return

    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    locks: list[LockEntity] = []
    for index in range(len(coordinator.data)):
        vehicle = coordinator.data[index]["data"]
        # Two conditions, because either one alone is wrong: a car that never
        # reports lock state has nothing to show, and a car that reports it
        # but declares the remote command unsupported would offer a control
        # that can only fail.
        doors = getattr(getattr(vehicle, "lock_status", None), "doors", None)
        if doors is None or not command_supported(vehicle, DOOR_CAPABILITY_FLAGS):
            continue
        locks.append(
            ToyotaVehicleLock(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=LOCK_DESCRIPTION,
                command_type=CommandType,
            )
        )

    async_add_entities(locks)


class ToyotaVehicleLock(ToyotaBaseEntity, LockEntity):
    """The whole vehicle, locked and unlocked in one command."""

    def __init__(self, *args: Any, command_type: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize the entity and retain the CommandType enum."""
        super().__init__(*args, **kwargs)
        self._command_type = command_type
        self._optimistic_locked: bool | None = None
        self._optimistic_until: float = 0.0

    @property
    def _reported_locked(self) -> bool | None:
        """Locked only when every opening the vehicle reports is locked."""
        return vehicle_locked(getattr(self.vehicle.lock_status, "doors", None))

    @property
    def _trunk_included(self) -> bool:
        """Whether the trunk gets its own command alongside the doors.

        The trunk counts towards ``is_locked``, so it has to be reachable by
        the command as well — otherwise locking a car whose trunk is open to
        central locking would report locked for the length of the optimistic
        window and then quietly fall back to unlocked.
        """
        doors = getattr(self.vehicle.lock_status, "doors", None)
        return getattr(doors, "trunk", None) is not None and command_supported(
            self.vehicle, TRUNK_CAPABILITY_FLAGS
        )

    @property
    def is_locked(self) -> bool | None:
        """Return whether the vehicle is locked, preferring a recent command."""
        if self._optimistic_locked is not None:
            if time.monotonic() < self._optimistic_until:
                return self._optimistic_locked
            self._optimistic_locked = None
        return self._reported_locked

    @callback
    def _handle_coordinator_update(self) -> None:
        """Retire the optimistic state as soon as the car confirms it."""
        super()._handle_coordinator_update()
        if (
            self._optimistic_locked is not None
            and self._reported_locked == self._optimistic_locked
        ):
            self._optimistic_locked = None
            self._optimistic_until = 0.0

    async def _send(self, commands: tuple[Any, ...], *, locked: bool) -> None:
        """Send the remote commands for one operation and reflect them.

        Every command has to be accepted: a lock that secured the doors but
        not the trunk has not locked the vehicle this entity describes, so a
        rejection on any of them is reported rather than averaged away.
        """
        for command in commands:
            try:
                response = await self.vehicle.post_command(command)
            except Exception as err:
                msg = f"Sending the command to Toyota failed: {err}"
                raise HomeAssistantError(msg) from err

            reason = rejection_reason(response)
            if reason is not None:
                _LOGGER.debug("Command %s rejected: %s", command, response)
                msg = f"Toyota did not accept the command ({reason})."
                raise HomeAssistantError(msg)

        self._optimistic_locked = locked
        self._optimistic_until = time.monotonic() + OPTIMISTIC_SECONDS
        self.async_write_ha_state()

        async def _refresh(_now: Any) -> None:  # noqa: ANN401
            await self.coordinator.async_request_refresh()

        # Tied to removal: without this the refresh still fires after the
        # entity is gone or the config entry is unloaded.
        self.async_on_remove(
            async_call_later(self.hass, REFRESH_DELAY_SECONDS, _refresh)
        )

    def _commands(self, *, locked: bool) -> tuple[Any, ...]:
        """Return the commands that lock or unlock everything reported."""
        if locked:
            door, trunk = self._command_type.DOOR_LOCK, self._command_type.TRUNK_LOCK
        else:
            door, trunk = (
                self._command_type.DOOR_UNLOCK,
                self._command_type.TRUNK_UNLOCK,
            )
        return (door, trunk) if self._trunk_included else (door,)

    async def async_lock(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Lock the vehicle."""
        await self._send(self._commands(locked=True), locked=True)

    async def async_unlock(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Unlock the vehicle."""
        await self._send(self._commands(locked=False), locked=False)
