"""Extra sensors for data the base integration does not expose.

Adds per-vehicle sensors:
- last_trip_score: overall driving score of the most recent cached trip
  (from the trips manager cache, same source as recent_trips).
- cabin_temperature: climate status current temperature.
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
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfSpeed, UnitOfTemperature
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import ToyotaBaseEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import StateType

    from .trips_manager import RecentTripsManager

_LOGGER = logging.getLogger(__name__)


def _trips_mgr(hass: HomeAssistant, entry_id: str) -> RecentTripsManager | None:
    """Return the per-entry RecentTripsManager, or None if not yet set up."""
    return hass.data.get(DOMAIN, {}).get(f"{entry_id}_trips_manager")


def _attr(obj: object, key: str, default: Any = None) -> Any:
    """Read ``key`` from ``obj`` whether it is a mapping or an attribute-bearing object.

    Some pytoyoda payloads (e.g. ``Dashboard.warning_lights``) are typed
    ``list[Any]`` and arrive as raw, undocumented JSON - which pydantic/httpx
    deserialise as plain ``dict``s, not attribute-bearing models. Reading
    those with ``getattr`` alone silently always returns ``default``.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _cabin_temperature(vehicle: object) -> float | None:
    """Return the cabin temperature value in degrees, or None if unavailable.

    ``ClimateStatus.current_temperature`` is a ``UnitValueModel`` (``value`` +
    ``unit``), not a bare number - the caller must unwrap ``.value``.
    """
    cs = getattr(vehicle, "climate_status", None)
    current = getattr(cs, "current_temperature", None)
    return current.value if current is not None else None


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
        """Return the driving score of the most recent trip.

        ``scores`` is a raw ``_ScoresModel.model_dump(by_alias=True)`` dict,
        so the overall score is keyed ``"global"`` (the JSON alias for
        pytoyoda's ``global_`` field), not ``"score"``.
        """
        trip = self._last_trip()
        if not trip:
            return None
        scores = trip.get("scores") or {}
        score = scores.get("global")
        return round(score, 1) if isinstance(score, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return trip timing/distance and driving-score attributes.

        The per-category breakdown (acceleration/braking/advice/constant
        speed) lives in the trip's ``scores`` dict (aliased ``constantSpeed``),
        not in ``behaviours`` - ``behaviours`` is an unrelated list of
        timestamped driving events.
        """
        trip = self._last_trip()
        if not trip:
            return None
        scores = trip.get("scores") or {}
        out: dict[str, Any] = {
            "start_ts": trip.get("start_ts"),
            "end_ts": trip.get("end_ts"),
            "distance_m": (trip.get("stats") or {}).get("distance_m"),
        }
        for src, name in (
            ("acceleration", "acceleration"),
            ("braking", "braking"),
            ("constantSpeed", "constant_speed"),
            ("advice", "advice"),
        ):
            v = scores.get(src)
            if v is not None:
                out[name] = v
        if scores.get("global") is not None:
            out["score"] = scores.get("global")
        return out


class ToyotaCabinTemperatureSensor(ToyotaExtraSensorBase):
    """Cabin temperature from climate status."""

    @property
    def native_value(self) -> StateType:
        """Return the cabin (interior) temperature."""
        return _cabin_temperature(self.vehicle)


class ToyotaWarningLightsSensor(ToyotaExtraSensorBase):
    """Dashboard warning lights count and detail."""

    def _lights(self) -> list[Any]:
        dash = getattr(self.vehicle, "dashboard", None)
        if dash is None:
            return []
        return getattr(dash, "warning_lights", None) or []

    @property
    def native_value(self) -> StateType:
        """Return the number of active dashboard warning lights."""
        return sum(
            1
            for light in self._lights()
            if _attr(light, "status") not in (None, False, "off")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the name/status/category detail for each warning light."""
        lights = self._lights()
        if not lights:
            return None
        return {
            "lights": [
                {
                    "name": _attr(light, "name"),
                    "status": _attr(light, "status"),
                    "category": _attr(light, "category"),
                }
                for light in lights
            ]
        }


class ToyotaServiceDetailSensor(ToyotaExtraSensorBase):
    """Full service history detail."""

    @property
    def native_value(self) -> StateType:
        """Return the date of the most recent service record."""
        hist = getattr(self.vehicle, "service_history", None) or []
        if not hist:
            return None
        sd = getattr(hist[-1], "service_date", None)
        return str(sd) if sd else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the full service history detail."""
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


def _speed_unit(metric_values: bool) -> str:  # noqa: FBT001
    """Return km/h or mph for the given account metric_values setting."""
    return (
        UnitOfSpeed.KILOMETERS_PER_HOUR if metric_values else UnitOfSpeed.MILES_PER_HOUR
    )


class ToyotaAverageSpeedWeekSensor(ToyotaExtraSensorBase):
    """Average speed from the current week summary."""

    @property
    def native_value(self) -> StateType:
        """Return the average speed for the current week."""
        stats = self.statistics
        if not stats:
            return None
        data = stats.get("week")
        return getattr(data, "average_speed", None) if data else None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return km/h or mph, matching the account's metric_values setting.

        pytoyoda's ``Summary.average_speed`` already reports in the unit the
        account is configured for (see ``metric_values`` / ``use_metric``),
        so the declared unit must follow ``self.metric_values`` rather than
        being hardcoded - otherwise imperial accounts show mph values
        mislabelled as km/h.
        """
        return _speed_unit(self.metric_values)


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
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
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
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        # No native_unit_of_measurement here: ToyotaAverageSpeedWeekSensor
        # overrides native_unit_of_measurement per-instance based on the
        # account's metric_values setting (km/h vs mph).
    ),
}

_CLASSES = {
    "last_trip_score": ToyotaLastTripScoreSensor,
    "cabin_temperature": ToyotaCabinTemperatureSensor,
    "warning_lights": ToyotaWarningLightsSensor,
    "last_service_detail": ToyotaServiceDetailSensor,
    "average_speed_week": ToyotaAverageSpeedWeekSensor,
}
