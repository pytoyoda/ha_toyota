"""Extra sensors for data the base integration does not expose.

Adds per-vehicle sensors:
- last_trip_score: overall driving score of the most recent cached trip
  (from the trips manager cache, same source as recent_trips).
- cabin_temperature: climate status current temperature.
- warning_lights: number of active vehicle warnings + detail.
- warning_message: text of the active vehicle warnings (e.g. low tire
  pressure), or OK.
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
from .utils import vehicle_has_climate_capability

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import StateType
    from pytoyoda.models.vehicle import Vehicle

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


def _dashboard_warnings(vehicle: object) -> list[Any] | None:
    """Return the raw health-status warning list, or None if not reported yet."""
    dash = getattr(vehicle, "dashboard", None)
    if dash is None:
        return None
    return getattr(dash, "warning_lights", None) or []


def _is_active_warning(entry: object) -> bool:
    """Return whether a health-status warning entry describes an active warning.

    Toyota's health-status endpoint lists one entry per active warning, keyed
    ``wngcode``/``wngdesc``/``wngtype``/``wngfilterType``/``severity``/
    ``wngdcmtime``. Entries shaped ``name``/``status`` are kept for
    compatibility and count only while their status is on.
    """
    if _attr(entry, "wngcode") or _attr(entry, "wngdesc"):
        return True
    return _attr(entry, "status") not in (None, False, "off")


def _warning_detail(entry: object) -> dict[str, Any]:
    """Flatten one health-status warning entry into readable attributes."""
    icons = _attr(entry, "wngicon") or []
    icon_url = next(
        (url for icon in icons if (url := _attr(icon, "deviceUrl"))),
        None,
    )
    return {
        "code": _attr(entry, "wngcode"),
        "description": _attr(entry, "wngdesc") or _attr(entry, "name"),
        "type": _attr(entry, "wngtype"),
        "category": _attr(entry, "wngfilterType") or _attr(entry, "category"),
        "severity": _attr(entry, "severity"),
        "reported_at": _attr(entry, "wngdcmtime"),
        "icon_url": icon_url,
    }


def _active_warnings(vehicle: object) -> list[dict[str, Any]] | None:
    """Return the detail of every active warning, or None if not reported yet."""
    entries = _dashboard_warnings(vehicle)
    if entries is None:
        return None
    return [_warning_detail(e) for e in entries if _is_active_warning(e)]


_MAX_STATE_LENGTH = 255


class ToyotaWarningLightsSensor(ToyotaExtraSensorBase):
    """Number of active vehicle warnings, with their detail."""

    @property
    def native_value(self) -> StateType:
        """Return the number of active vehicle warnings."""
        warnings = _active_warnings(self.vehicle)
        return None if warnings is None else len(warnings)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return code, description, category, severity, time for each warning."""
        warnings = _active_warnings(self.vehicle)
        if not warnings:
            return None
        return {"warnings": warnings}


class ToyotaWarningMessageSensor(ToyotaExtraSensorBase):
    """Text of the active vehicle warnings, as reported by Toyota."""

    @property
    def native_value(self) -> StateType:
        """Return the warning descriptions, or ``OK`` when none are active."""
        warnings = _active_warnings(self.vehicle)
        if warnings is None:
            return None
        if not warnings:
            return "OK"
        text = ", ".join(w["description"] or w["code"] or "?" for w in warnings)
        return text[:_MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the warning codes behind the message."""
        warnings = _active_warnings(self.vehicle)
        if not warnings:
            return None
        return {"codes": [w["code"] for w in warnings]}


class ToyotaServiceDetailSensor(ToyotaExtraSensorBase):
    """Full service history detail."""

    @property
    def native_value(self) -> StateType:
        """Return the date of the most recent service record."""
        hist = getattr(self.vehicle, "service_history", None) or []
        if not hist:
            return None
        sd = getattr(hist[0], "service_date", None)
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
    "warning_message": SensorEntityDescription(
        key="warning_message",
        translation_key="warning_message",
        icon="mdi:alert-circle-outline",
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
    "warning_message": ToyotaWarningMessageSensor,
    "last_service_detail": ToyotaServiceDetailSensor,
    "average_speed_week": ToyotaAverageSpeedWeekSensor,
}

# Per-key capability gate, checked before an extra sensor is created for a
# vehicle. Keys absent here are always created (matches the pre-existing
# behaviour for notifications/warning_lights/warning_message/
# last_service_detail/average_speed_week - none of them read from an
# endpoint pytoyoda gates on a capability flag). cabin_temperature reads
# climate_status, which pytoyoda only ever populates for climate-capable
# vehicles (see vehicle_has_climate_capability) - without this gate the
# sensor is created for every vehicle and reads permanently unknown on
# non-climate-capable ones.
CAPABILITY_CHECKS: dict[str, Callable[[Vehicle], bool]] = {
    "cabin_temperature": vehicle_has_climate_capability,
}
