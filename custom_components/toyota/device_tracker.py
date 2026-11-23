"""Device tracker platform for Toyota Connected Services."""

# pylint: disable=W0212, W0511

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, ICON_PARKING
from .entity import ToyotaBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import VehicleData

PARKING_TRACKER_DESCRIPTION: EntityDescription = EntityDescription(
    key="parking_location",
    translation_key="parking_location",
    icon=ICON_PARKING,
)

ATTR_LOCATION_TIMESTAMP = "location_timestamp"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the Toyota Connected Services tracker from config entry."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    async_add_devices(
        ToyotaParkingTracker(
            coordinator=coordinator,
            entry_id=entry.entry_id,
            vehicle_index=index,
            description=PARKING_TRACKER_DESCRIPTION,
        )
        for index, vehicle in enumerate(coordinator.data)
        if getattr(
            getattr(vehicle["data"]._vehicle_info, "extended_capabilities", False),  # noqa : SLF001
            "last_parked_capable",
            False,
        )
        or getattr(
            getattr(vehicle["data"]._vehicle_info, "features", False),  # noqa : SLF001
            "last_parked",
            False,
        )
    )


class ToyotaParkingTracker(ToyotaBaseEntity, TrackerEntity, RestoreEntity):
    """Toyota Connected Services device tracker.

    The status payload often comes without a parking fix: while driving, for a
    while after parking, or when the car sits somewhere without mobile
    coverage (an underground garage). The tracker keeps the last valid fix
    until a new one arrives instead of dropping to ``unknown``, and restores it
    across restarts. ``location_timestamp`` says how old that fix is.
    """

    coordinator: DataUpdateCoordinator[list[VehicleData]]
    _last_fix: tuple[float, float, datetime | str | None] | None = None

    def _fix(self) -> tuple[float, float, datetime | str | None] | None:
        """Return the newest valid fix, remembering it for later cycles."""
        location = self.vehicle.location
        latitude = getattr(location, "latitude", None)
        longitude = getattr(location, "longitude", None)
        if latitude is not None and longitude is not None:
            self._last_fix = (latitude, longitude, getattr(location, "timestamp", None))
        return self._last_fix

    async def async_added_to_hass(self) -> None:
        """Restore the last fix so a restart without one doesn't blank it."""
        if (last_state := await self.async_get_last_state()) is not None:
            attrs = last_state.attributes
            latitude = attrs.get(ATTR_LATITUDE)
            longitude = attrs.get(ATTR_LONGITUDE)
            if latitude is not None and longitude is not None:
                self._last_fix = (
                    latitude,
                    longitude,
                    attrs.get(ATTR_LOCATION_TIMESTAMP),
                )
        await super().async_added_to_hass()

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        fix = self._fix()
        return fix[0] if fix else None

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        fix = self._fix()
        return fix[1] if fix else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the shown fix was acquired."""
        fix = self._fix()
        timestamp = fix[2] if fix else None
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()
        return {ATTR_LOCATION_TIMESTAMP: timestamp} if timestamp else {}

    @property
    def source_type(self) -> SourceType:
        """Return the source type, eg gps or router, of the device."""
        return SourceType.GPS

    @property
    def entity_picture(self) -> str | None:
        """Return entity picture."""
        return self.vehicle._vehicle_info.image  # noqa : SLF001
