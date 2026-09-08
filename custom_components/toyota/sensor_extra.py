"""Extra sensors for data the base integration does not expose.

Adds per-vehicle sensors:
- last_trip_score: overall driving score of the most recent cached trip
  (from the trips manager cache, same source as recent_trips).
- cabin_temperature: climate status current temperature.
- notifications: Toyota notification count + detail attributes.
- warning_lights: dashboard warning lights count + detail.
- last_service_detail: full service history detail (operations, notes,
  dealer, ro_number) with newest record's date as state.
- average_speed_week: average speed from current week summary.

Follows the ToyotaBaseEntity pattern so entities attach to the same
device and unique-id namespace as the base integration sensors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfSpeed, UnitOfTemperature
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import ToyotaBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import VehicleData

_LOGGER = logging.getLogger(__name__)


def _trips_mgr(hass, entry_id):
    return hass.data.get(DOMAIN, {}).get(f"{entry_id}_trips_manager")


class ToyotaExtraSensorBase(ToyotaBaseEntity, SensorEntity):
    """Shared helpers for extra sensors."""

    def _last_trip(self) -> dict | None:
        mgr = _trips_mgr(self.hass, self._entry_id)
        vin = getattr(self.vehicle, "vin", None)
        if mgr is None or not vin:
            return None
        trips = mgr.cache.get(vin) or []
        return trips[0] if trips else None


class ToyotaLastTripScoreSensor(ToyotaExtraSensorBase):
    """Driving score of the most recent trip (cache the same as recent_trips)."""

    @property
    def native_value(self) -> StateType:
        trip = self._last_trip()
        if not trip:
            return None
        scores = trip.get("scores") or {}
        score = scores.get("score")
        return round(score, 1) if isinstance(score, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        trip = self._last_trip()
        if not trip:
            return None
        scores = trip.get("scores") or {}
        behaviours = trip.get("behaviours") or {}
        out: dict[str, Any] = {
            "start_ts": trip.get("start_ts"),
            "end_ts": trip.get("end_ts"),
            "distance_m": (trip.get("stats") or {}).get("distance_m"),
        }
        for src, name in (
            ("acceleration", "acceleration"),
            ("braking", "braking"),
            ("constant_speed", "constant_speed"),
            ("advice", "advice"),
        ):
            v = behaviours.get(src)
            if v is not None:
                out[name] = v
        if scores.get("score") is not None:
            out["score"] = scores.get("score")
        return out


class ToyotaCabinTemperatureSensor(ToyotaExtraSensorBase):
    """Cabin temperature from climate status."""

    @property
    def native_value(self) -> StateType:
        cs = getattr(self.vehicle, "climate_status", None)
        return getattr(cs, "current_temperature", None)

    @property
    def unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS


class ToyotaNotificationsSensor(ToyotaExtraSensorBase):
    """Toyota notifications count and detail."""

    @property
    def native_value(self) -> StateType:
        notes = getattr(self.vehicle, "notifications", None) or []
        return len(notes)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        notes = getattr(self.vehicle, "notifications", None) or []
        if not notes:
            return None
        return {
            "unread": sum(1 for n in notes if not getattr(n, "read", True)),
            "messages": [
                {
                    "date": str(getattr(n, "date", "")),
                    "category": getattr(n, "category", None),
                    "type": getattr(n, "type", None),
                    "message": getattr(n, "message", None),
                    "read": getattr(n, "read", None),
                }
                for n in notes[:20]
            ],
        }


class ToyotaWarningLightsSensor(ToyotaExtraSensorBase):
    """Dashboard warning lights count and detail."""

    @property
    def native_value(self) -> StateType:
        dash = getattr(self.vehicle, "dashboard", None)
        if dash is None:
            return None
        lights = getattr(dash, "warning_lights", None) or []
        return sum(
            1 for l in lights if getattr(l, "status", None) not in (None, False, "off")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        dash = getattr(self.vehicle, "dashboard", None)
        if dash is None:
            return None
        lights = getattr(dash, "warning_lights", None) or []
        return {
            "lights": [
                {
                    "name": getattr(l, "name", None),
                    "status": getattr(l, "status", None),
                    "category": getattr(l, "category", None),
                }
                for l in lights
            ]
        }


class ToyotaServiceDetailSensor(ToyotaExtraSensorBase):
    """Full service history detail."""

    @property
    def native_value(self) -> StateType:
        hist = getattr(self.vehicle, "service_history", None) or []
        if not hist:
            return None
        sd = getattr(hist[-1], "service_date", None)
        return str(sd) if sd else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        hist = getattr(self.vehicle, "service_history", None) or []
        if not hist:
            return None
        return {
            "services": [
                {
                    "date": str(getattr(s, "service_date", None) or ""),
                    "odometer": getattr(s, "odometer", None),
                    "category": getattr(s, "service_category", None),
                    "operations": getattr(s, "operations_performed", None),
                    "notes": getattr(s, "notes", None),
                    "provider": getattr(s, "service_provider", None),
                    "dealer": getattr(s, "servicing_dealer", None),
                    "ro_number": getattr(s, "ro_number", None),
                    "customer_created": getattr(s, "customer_created_record", None),
                }
                for s in hist
            ]
        }


class ToyotaAverageSpeedWeekSensor(ToyotaExtraSensorBase):
    """Average speed from the current week summary."""

    @property
    def native_value(self) -> StateType:
        stats = self.statistics
        if not stats:
            return None
        data = stats.get("week")
        return getattr(data, "average_speed", None) if data else None

    @property
    def unit_of_measurement(self) -> str:
        return UnitOfSpeed.KILOMETERS_PER_HOUR


DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    "last_trip_score": SensorEntityDescription(
        key="last_trip_score",
        translation_key="last_trip_score",
        icon="mdi:steering",
    ),
    "cabin_temperature": SensorEntityDescription(
        key="cabin_temperature",
        translation_key="cabin_temperature",
        icon="mdi:thermometer",
    ),
    "notifications": SensorEntityDescription(
        key="notifications",
        translation_key="notifications",
        icon="mdi:bell-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "warning_lights": SensorEntityDescription(
        key="warning_lights",
        translation_key="warning_lights",
        icon="mdi:car-tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "last_service_detail": SensorEntityDescription(
        key="last_service_detail",
        translation_key="last_service_detail",
        icon="mdi:file-document-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "average_speed_week": SensorEntityDescription(
        key="average_speed_week",
        translation_key="average_speed_week",
        icon="mdi:speedometer",
    ),
}

_CLASSES = {
    "last_trip_score": ToyotaLastTripScoreSensor,
    "cabin_temperature": ToyotaCabinTemperatureSensor,
    "notifications": ToyotaNotificationsSensor,
    "warning_lights": ToyotaWarningLightsSensor,
    "last_service_detail": ToyotaServiceDetailSensor,
    "average_speed_week": ToyotaAverageSpeedWeekSensor,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the extra Toyota sensors."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    devices = []
    for index in range(len(coordinator.data)):
        for key, cls in _CLASSES.items():
            devices.append(
                cls(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    vehicle_index=index,
                    description=DESCRIPTIONS[key],
                )
            )
    async_add_devices(devices)
