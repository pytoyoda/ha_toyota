"""Per-vehicle refresh-status button.

Wraps the toyota.refresh_vehicle_status service with a one-tap dashboard
entity. Each vehicle gets one button; pressing it triggers the same wake
POST + status poll that the service does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription

from .const import (
    CONF_MAX_RECENT_TRIPS,
    DEFAULT_MAX_RECENT_TRIPS,
    DOMAIN,
)
from .entity import ToyotaBaseEntity

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
