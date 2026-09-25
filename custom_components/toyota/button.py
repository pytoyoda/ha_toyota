"""Per-vehicle refresh-status button, plus a remote buzzer command button.

The refresh buttons wrap the toyota.refresh_vehicle_status/etc. services
with a one-tap dashboard entity. Each vehicle gets one button; pressing it
triggers the same wake POST + status poll that the service does.

The buzzer button (#428) sends pytoyoda's ``CommandType.BUZZER_WARNING``
remote command directly - there is no corresponding HA service for it, and
unlike the refresh buttons it isn't idempotent/repeatable-with-a-limit, so
it doesn't need one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.exceptions import HomeAssistantError
from pytoyoda.models.endpoints.command import CommandType

from .const import (
    CONF_MAX_RECENT_TRIPS,
    DEFAULT_MAX_RECENT_TRIPS,
    DOMAIN,
)
from .entity import ToyotaBaseEntity
from .sensor import ELECTRIC_STATUS_VEHICLE_TYPES, get_vehicle_capability
from .utils import command_failure_reason

# Default fetch size for the manual button when auto-fetch is off
# (max_recent_trips=0). Picked as a sensible "show me the last few drives".
_BUTTON_LIMIT_FALLBACK = 5

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import VehicleData


REFRESH_BUTTON_DESCRIPTION = ButtonEntityDescription(
    key="refresh_vehicle_status",
    translation_key="refresh_vehicle_status",
    name="Refresh vehicle status",
    icon="mdi:refresh-circle",
)

REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION = ButtonEntityDescription(
    key="refresh_recent_trips",
    translation_key="refresh_recent_trips",
    name="Refresh recent trips",
    icon="mdi:refresh-auto",
)

REFRESH_ELECTRIC_REALTIME_STATUS_BUTTON_DESCRIPTION = ButtonEntityDescription(
    key="refresh_electric_realtime_status",
    translation_key="refresh_electric_realtime_status",
    name="Refresh electric realtime status",
    icon="mdi:battery-sync",
)

BUZZER_BUTTON_DESCRIPTION = ButtonEntityDescription(
    key="sound_buzzer",
    translation_key="sound_buzzer",
    name="Sound buzzer",
    icon="mdi:bullhorn",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota button entities."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]
    buttons: list[ButtonEntity] = []
    for index in range(len(coordinator.data)):
        vehicle = coordinator.data[index]["data"]
        buttons.append(
            ToyotaRefreshStatusButton(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=REFRESH_BUTTON_DESCRIPTION,
            )
        )
        buttons.append(
            ToyotaRefreshRecentTripsButton(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION,
            )
        )
        if (
            get_vehicle_capability(vehicle, "econnect_vehicle_status_capable")
            or vehicle.type in ELECTRIC_STATUS_VEHICLE_TYPES
        ):
            buttons.append(
                ToyotaRefreshElectricRealtimeStatusButton(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    vehicle_index=index,
                    description=REFRESH_ELECTRIC_REALTIME_STATUS_BUTTON_DESCRIPTION,
                )
            )
        if get_vehicle_capability(vehicle, "buzzer_capable"):
            buttons.append(
                ToyotaBuzzerButton(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    vehicle_index=index,
                    description=BUZZER_BUTTON_DESCRIPTION,
                )
            )
    async_add_entities(buttons)


class ToyotaRefreshStatusButton(ToyotaBaseEntity, ButtonEntity):
    """One-tap wrapper around toyota.refresh_vehicle_status for one VIN."""

    async def async_press(self) -> None:
        """Fire toyota.refresh_vehicle_status for this vehicle's device."""
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get_device(
            identifiers={(DOMAIN, self.vehicle.vin or "")}
        )
        if device is None:
            return
        await self.hass.services.async_call(
            DOMAIN,
            "refresh_vehicle_status",
            {"device_id": [device.id]},
            blocking=False,
        )


class ToyotaRefreshRecentTripsButton(ToyotaBaseEntity, ButtonEntity):
    """One-tap wrapper around toyota.refresh_recent_trips for one VIN.

    Limit defaults to the user's max_recent_trips when set; falls back to
    _BUTTON_LIMIT_FALLBACK (5) when auto-fetch is disabled (max=0). Users
    who want a different one-tap fetch size should use the service call
    with an explicit ``limit`` field.
    """

    async def async_press(self) -> None:
        """Fire toyota.refresh_recent_trips for this vehicle's device."""
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get_device(
            identifiers={(DOMAIN, self.vehicle.vin or "")}
        )
        if device is None:
            return
        # Resolve the limit from the config entry's options. self.coordinator
        # exposes its config entry via the standard HA pattern.
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        max_trips = int(
            entry.options.get(CONF_MAX_RECENT_TRIPS, DEFAULT_MAX_RECENT_TRIPS)
            if entry is not None
            else DEFAULT_MAX_RECENT_TRIPS
        )
        limit = max_trips if max_trips > 0 else _BUTTON_LIMIT_FALLBACK
        await self.hass.services.async_call(
            DOMAIN,
            "refresh_recent_trips",
            {"device_id": [device.id], "limit": limit},
            blocking=False,
        )


class ToyotaRefreshElectricRealtimeStatusButton(ToyotaBaseEntity, ButtonEntity):
    """One-tap wrapper around toyota.refresh_electric_realtime_status.

    Only added for vehicles that support the EV/electric status endpoint.
    Wakes the vehicle to force a fresh battery/charging state read; use
    sparingly as each call uses cellular airtime and a small amount of
    12V battery.
    """

    async def async_press(self) -> None:
        """Fire toyota.refresh_electric_realtime_status for this vehicle."""
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get_device(
            identifiers={(DOMAIN, self.vehicle.vin or "")}
        )
        if device is None:
            return
        await self.hass.services.async_call(
            DOMAIN,
            "refresh_electric_realtime_status",
            {"device_id": [device.id]},
            blocking=False,
        )


class ToyotaBuzzerButton(ToyotaBaseEntity, ButtonEntity):
    """One-tap remote buzzer/horn-warning command (see #428)."""

    async def async_press(self) -> None:
        """Send the buzzer-warning remote command to this vehicle."""
        try:
            response = await self.vehicle.post_command(CommandType.BUZZER_WARNING)
        except Exception as err:
            msg = "Toyota could not send the buzzer command"
            raise HomeAssistantError(msg) from err
        if reason := command_failure_reason(response):
            raise HomeAssistantError(reason)
