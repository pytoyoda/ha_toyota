"""Lock platform for Toyota integration.

Exposes a single lock entity per vehicle that sends door-lock / door-unlock
remote commands through the pytoyoda ``post_command`` API.  The entity is
only registered when the vehicle reports ``door_lock_unlock_capable`` (via
``extended_capabilities``) or ``dlock_unlock_capable`` (via
``remote_service_capabilities``).

After a lock/unlock command is dispatched the integration triggers
``toyota.refresh_vehicle_status`` so the coordinator fetches fresh door-state
data without waiting for the next polling cycle.  While the refresh is in
flight the entity uses an *optimistic* assumed state (``_attr_assumed_state``
is ``True``) so that HA shows the expected lock/unlock state immediately with
the "assumed" badge instead of reverting to "unknown".  During the command
flight the entity also reports ``is_locking`` / ``is_unlocking`` so the UI
can show an in-progress spinner.

The gateway response code is checked before applying the optimistic state:
if Toyota rejects the command (HTTP 4xx / 5xx application code) the entity
does NOT assert the commanded state and logs a warning instead.

The optimistic state is cleared as soon as the coordinator delivers the first
genuinely fresh status update after the command.  On HA restart the last
known lock state is restored from HA's persistent storage so the entity never
shows as *unknown* immediately after startup.

.. note::
    Some Toyota models do **not** support remote unlock when the doors were
    locked with the physical key rather than via the app or remote.  In that
    case the ``door-unlock`` command will be rejected by the Toyota gateway
    and Home Assistant will surface the error as a service-call failure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from pytoyoda.models.endpoints.command import CommandType

from .const import DOMAIN
from .entity import ToyotaBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
    from pytoyoda.models.vehicle import Vehicle

    from . import VehicleData

_LOGGER = logging.getLogger(__name__)

# HTTP response codes >= this value from post_command() indicate that the
# Toyota gateway rejected the command (4xx client error, 5xx server error).
# When the command is rejected the entity must NOT assert an optimistic state
# it could not actually achieve.
_HTTP_REJECTION_THRESHOLD = 400

DOOR_LOCK_DESCRIPTION = LockEntityDescription(
    key="door_lock",
    translation_key="door_lock",
)


def _is_lock_capable(vehicle: Vehicle) -> bool:
    """Return True when the vehicle supports remote door lock / unlock.

    Checks two independent capability models exposed by pytoyoda so that
    the entity is created whenever at least one flag signals support,
    regardless of which API response populated it.
    """
    extended = getattr(vehicle._vehicle_info, "extended_capabilities", None)  # noqa: SLF001
    if getattr(extended, "door_lock_unlock_capable", False):
        return True

    remote = getattr(vehicle._vehicle_info, "remote_service_capabilities", None)  # noqa: SLF001
    return bool(getattr(remote, "dlock_unlock_capable", False))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota lock entities from a config entry."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    async_add_entities(
        ToyotaLockEntity(
            coordinator=coordinator,
            entry_id=entry.entry_id,
            vehicle_index=index,
            description=DOOR_LOCK_DESCRIPTION,
        )
        for index, vehicle_data in enumerate(coordinator.data)
        if _is_lock_capable(vehicle_data["data"])
    )


class ToyotaLockEntity(ToyotaBaseEntity, LockEntity, RestoreEntity):
    """Lock entity representing the central door-lock state of a Toyota vehicle.

    State is derived from the driver-seat door lock reported by the Toyota
    Connected Services status endpoint.  ``None`` (unknown) is returned when
    the car has not yet transmitted a status update so that HA shows the
    entity as *unknown* rather than falsely locked or unlocked.

    ``assumed_state`` is a dynamic property that returns ``True`` only while
    an optimistic command is in-flight (i.e. ``_assumed_locked is not None``).
    Once the coordinator delivers confirmed data and clears ``_assumed_locked``
    the "assumed" badge disappears from the HA UI so the user knows the state
    is now car-confirmed.

    Sending a lock/unlock command via ``async_lock`` / ``async_unlock``
    dispatches the remote command and then requests an immediate coordinator
    refresh so the updated state is reflected in HA without waiting for the
    next polling cycle.  ``is_locking`` / ``is_unlocking`` are set for the
    duration of the command so the UI can show an in-progress spinner.

    If the Toyota gateway rejects the command (response code >= 400) the
    entity does NOT apply the optimistic state and logs a warning so the
    failure is visible without crashing HA.

    On HA restart the last known lock state is restored from HA's persistent
    storage so the entity never shows as *unknown* immediately after startup.
    """

    def __init__(self, **kwargs: object) -> None:
        """Initialise the lock entity with no optimistic state."""
        super().__init__(**kwargs)
        # Optimistic lock state set immediately after a successful command.
        # Cleared on the next coordinator update so real data takes priority.
        self._assumed_locked: bool | None = None
        # Last confirmed lock state (True/False). Used as fallback when the
        # API returns None for driver_seat.locked so the entity never reverts
        # to "unknown" once a real state has been observed.  Also restored
        # from HA persistent storage on restart via async_added_to_hass so
        # the entity does not flip to "unknown" on every HA boot.
        self._last_known_locked: bool | None = None

    @property
    def assumed_state(self) -> bool:
        """Return True only while an optimistic command is in-flight.

        HA renders the "assumed" badge on the lock card when this property is
        True.  By making it dynamic (instead of using the static class-level
        ``_attr_assumed_state = True``) the badge is shown only while the
        entity is waiting for the coordinator to confirm the commanded state,
        and disappears once real car data arrives.
        """
        return self._assumed_locked is not None

    async def async_added_to_hass(self) -> None:
        """Restore last known lock state from HA persistent storage on restart.

        Called by HA when the entity is added to hass (including after a
        restart).  If a previous state was recorded, it is used to initialise
        ``_last_known_locked`` so the entity immediately shows the last known
        value instead of *unknown* while waiting for the first API response.
        The restored value is overwritten as soon as the coordinator delivers
        fresh data from the Toyota status endpoint.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("locked", "unlocked"):
            self._last_known_locked = last_state.state == "locked"
            _LOGGER.debug(
                "Restored last known lock state '%s' for vin=...%s",
                last_state.state,
                (getattr(self.vehicle, "vin", None) or "")[-6:],
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state only when genuinely fresh data arrives.

        If the coordinator fired because a refresh failed (served cached or stub
        data), keep the optimistic state so the UI does not revert to 'unknown'
        while the car is still processing the command.  Real data displaces the
        optimistic state on the next successful status fetch.
        """
        if self._assumed_locked is not None:
            try:
                vd = self.coordinator.data[self.index]
                is_fresh = (
                    not vd.get("is_cached")
                    and vd.get("last_successful_fetch") is not None
                )
            except (IndexError, TypeError):
                is_fresh = False
            if is_fresh:
                # Preserve the optimistic state as a last-known fallback
                # before discarding it.  Some Toyota models omit
                # driver_seat.locked from the API response; without this,
                # _last_known_locked stays None and the entity flips to
                # "unknown" as soon as the optimistic state is cleared.
                # is_locked() will overwrite this with the real API value
                # if the status endpoint does include driver_seat.locked.
                self._last_known_locked = self._assumed_locked
                self._assumed_locked = None
        super()._handle_coordinator_update()

    @property
    def icon(self) -> str:
        """Return lock/unlock icon based on current state."""
        if self.is_locked is False:
            return "mdi:car-door-lock-open"
        return "mdi:car-door-lock"

    @property
    def is_locked(self) -> bool | None:
        """Return True when the driver-seat door is locked, None when unknown.

        Returns the optimistic state if a lock/unlock command has been sent
        and the coordinator has not yet confirmed the result.  Otherwise
        reads the live state from the status endpoint.

        When the API returns None for driver_seat.locked (e.g. Toyota omits
        the field in certain status responses) the last real state is returned
        as a fallback so the entity does not unnecessarily revert to "unknown".
        External state changes (key fob, MyToyota app, etc.) are still picked
        up correctly whenever the API does include a valid value.
        """
        if self._assumed_locked is not None:
            return self._assumed_locked
        lock_status = getattr(self.vehicle, "lock_status", None)
        if lock_status is not None:
            doors = getattr(lock_status, "doors", None)
            if doors is not None:
                driver_seat = getattr(doors, "driver_seat", None)
                real_state = getattr(driver_seat, "locked", None)
                if real_state is not None:
                    # Update last-known state whenever the API reports a value,
                    # regardless of whether the change came from HA or externally
                    # (key fob, MyToyota app, etc.).
                    self._last_known_locked = real_state
                    return real_state
        # API returned no lock data - keep the last known state so the entity
        # does not flip to "unknown" while the car is temporarily silent.
        return self._last_known_locked

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return last-updated timestamp as a diagnostic attribute."""
        lock_status = getattr(self.vehicle, "lock_status", None)
        return {
            "last_updated": getattr(lock_status, "last_updated", None)
            if lock_status
            else None,
        }

    async def async_lock(self, **_kwargs: object) -> None:
        """Send door-lock command to the vehicle."""
        await self._async_send_command(CommandType.DOOR_LOCK, assumed_locked=True)

    async def async_unlock(self, **_kwargs: object) -> None:
        """Send door-unlock command to the vehicle."""
        await self._async_send_command(CommandType.DOOR_UNLOCK, assumed_locked=False)

    async def _async_send_command(
        self, command: CommandType, *, assumed_locked: bool
    ) -> None:
        """Dispatch *command*, apply optimistic state, and schedule a refresh.

        Sets ``is_locking`` / ``is_unlocking`` for the duration of the command
        so the UI can render an in-progress spinner.  Checks the gateway
        response code before asserting the optimistic state: if the command is
        rejected (code >= _HTTP_REJECTION_THRESHOLD) the entity logs a warning
        and does NOT apply the commanded state.  On success the optimistic
        state is held until the coordinator delivers fresh telemetry.

        Any unexpected exception from post_command propagates to the caller so
        Home Assistant can surface it as a service-call failure.
        """
        _LOGGER.debug(
            "Sending remote command %s to vehicle vin=...%s",
            command.value,
            (self.vehicle.vin or "")[-6:],
        )
        self._attr_is_locking = assumed_locked
        self._attr_is_unlocking = not assumed_locked
        self.async_write_ha_state()
        try:
            status = await self.vehicle.post_command(command)
            code = getattr(status, "code", None)
            if code is not None and code >= _HTTP_REJECTION_THRESHOLD:
                _LOGGER.warning(
                    "%s for %s returned code %s: %s",
                    command.value,
                    getattr(self.vehicle, "alias", self.vehicle.vin),
                    code,
                    getattr(status, "message", None),
                )
                # Gateway rejected the command; do NOT assert a state we
                # could not achieve.  Return without applying optimistic state
                # so the entity stays at its last known value.
                return

            # Command accepted: assert the commanded state until telemetry
            # confirms the change (which can take minutes on Toyota's side).
            self._assumed_locked = assumed_locked
            self.async_write_ha_state()

            await self._async_request_refresh()
        finally:
            self._attr_is_locking = False
            self._attr_is_unlocking = False
            self.async_write_ha_state()

    async def _async_request_refresh(self) -> None:
        """Trigger refresh_vehicle_status for this vehicle's HA device."""
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get_device(
            identifiers={(DOMAIN, self.vehicle.vin or "")}
        )
        if device is None:
            _LOGGER.debug(
                "No HA device found for vin=...%s; skipping status refresh",
                (self.vehicle.vin or "")[-6:],
            )
            return

        await self.hass.services.async_call(
            DOMAIN,
            "refresh_vehicle_status",
            {"device_id": [device.id]},
            blocking=False,
        )
