"""Utilities for Toyota integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .const import CONF_BRAND_MAPPING, REMOTE_DISPLAY_NAMES

if TYPE_CHECKING:
    from datetime import timedelta

    from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
    from pytoyoda.models.summary import Summary
    from pytoyoda.models.vehicle import Vehicle

_HTTP_CLIENT_ERROR = 400
_HTTP_SERVER_ERROR = 600
_FAILURE_STATUSES = frozenset({"error", "failed", "failure", "rejected"})
_HTTP_CODE = re.compile(r"(?:^|[^0-9])([45][0-9]{2})(?:$|[^0-9])")


def command_failure_reason(response: object) -> str | None:
    """Return a safe reason when Toyota explicitly rejects a remote command.

    Shared by every entity that posts a ``pytoyoda`` remote command (door
    lock, hazard lights, buzzer, ...) so the same HTTP/status-shape parsing
    isn't duplicated per platform.
    """
    errors = getattr(response, "errors", None)
    if errors:
        return "Toyota reported an error"
    return _http_failure_reason(
        getattr(response, "code", None)
    ) or _status_failure_reason(getattr(response, "status", None))


def _http_failure_reason(code: object) -> str | None:
    """Return a reason for an HTTP client/server response code."""
    if not isinstance(code, int):
        return None
    if _HTTP_CLIENT_ERROR <= code < _HTTP_SERVER_ERROR:
        return f"Toyota returned HTTP {code}"
    return None


def _status_failure_reason(status: object) -> str | None:
    """Return a reason for a known failure status or gateway message."""
    if isinstance(status, str):
        return (
            "Toyota rejected the command"
            if status.lower() in _FAILURE_STATUSES
            else None
        )
    messages = getattr(status, "messages", None) or ()
    return (
        "Toyota rejected the command" if _has_http_failure_message(messages) else None
    )


def _has_http_failure_message(messages: object) -> bool:
    """Return whether a gateway message contains an HTTP 4xx or 5xx code."""
    return any(
        isinstance(response_code := getattr(message, "response_code", ""), str)
        and _HTTP_CODE.search(response_code)
        for message in messages
    )


def td_to_hoursminutes(td: timedelta | None) -> str | None:
    """Convert a timedelta to hours and minutes string."""
    if td is None:
        return None
    total_minutes = int(td.total_seconds()) // 60
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}:{minutes}"


def round_number(number: float | None, places: int = 0) -> int | float | None:
    """Round a number if it is not None."""
    return None if number is None else round(number, places)


def mask_string(string: str | None) -> str | None:
    """Mask all except the last 5 digits of a given string with asteriks."""
    if string:
        max_digits = 5
        return (
            "*" * (len(string) - max_digits) + string[-max_digits:]
            if len(string) >= max_digits
            else "*****"
        )
    return string


def format_vin_sensor_attributes(
    vehicle_info: VehicleGuidModel,
) -> dict[str, str | bool | dict[str, bool] | None]:
    """Format and returns vin sensor attributes."""
    return {
        "Contract_id": mask_string(vehicle_info.contract_id),
        "IMEI": mask_string(vehicle_info.imei),
        "Katashiki_code": vehicle_info.katashiki_code,
        "ASI_code": vehicle_info.asi_code,
        "Brand": CONF_BRAND_MAPPING.get(vehicle_info.brand)
        if vehicle_info.brand
        else None,
        "Car_line_name": vehicle_info.car_line_name,
        "Car_model_year": vehicle_info.car_model_year,
        "Car_model_name": vehicle_info.car_model_name,
        "Color": vehicle_info.color,
        "Generation": vehicle_info.generation,
        "Manufactured_date": None
        if vehicle_info.manufactured_date is None
        else vehicle_info.manufactured_date.strftime("%Y-%m-%d"),
        "Date_of_first_use": None
        if vehicle_info.date_of_first_use is None
        else vehicle_info.date_of_first_use.strftime("%Y-%m-%d"),
        "Transmission_type": vehicle_info.transmission_type,
        "Fuel_type": vehicle_info.fuel_type,
        "Electrical_platform_code": vehicle_info.electrical_platform_code,
        "EV_vehicle": vehicle_info.ev_vehicle,
        "Features": {
            key: value
            for key, value in vehicle_info.features.model_dump().items()
            if value is True
        }
        if vehicle_info.features
        else None,
        "Extended_capabilities": {
            key: value
            for key, value in vehicle_info.extended_capabilities.model_dump().items()
            if value is True
        }
        if vehicle_info.extended_capabilities
        else None,
        "Remote_service_capabilities": {
            key: value
            for key, value in vehicle_info.remote_service_capabilities.model_dump().items()  # noqa: E501
            if value is True
        }
        if vehicle_info.remote_service_capabilities
        else None,
    }


def format_statistics_attributes(
    statistics: Summary, vehicle_info: VehicleGuidModel
) -> dict[str, list[str] | float | str | None]:
    """Format and returns statistics attributes."""
    attr = {
        "Average_speed": round(statistics.average_speed, 1)
        if statistics.average_speed
        else None,
        "Countries": statistics.countries or [],
        "Duration": str(statistics.duration) if statistics.duration else None,
        "Hybrid_score": round(statistics.hybrid_score, 1)
        if statistics.hybrid_score is not None
        else None,
    }

    if vehicle_info.fuel_type is not None:
        attr |= {
            "Total_fuel_consumed": round(statistics.fuel_consumed, 3)
            if statistics.fuel_consumed
            else None,
            "Average_fuel_consumed": round(statistics.average_fuel_consumed, 3)
            if statistics.average_fuel_consumed
            else None,
        }

    if getattr(
        getattr(vehicle_info, "extended_capabilities", False),
        "hybrid_pulse",
        False,
    ) or getattr(
        getattr(vehicle_info, "extended_capabilities", False),
        "econnect_vehicle_status_capable",
        False,
    ):
        attr |= {
            "EV_distance": round(statistics.ev_distance, 1)
            if statistics.ev_distance
            else None,
            "EV_duration": str(statistics.ev_duration)
            if statistics.ev_duration
            else None,
        }

    attr |= {
        "From_date": statistics.from_date.strftime("%Y-%m-%d"),
        "To_date": statistics.to_date.strftime("%Y-%m-%d"),
    }

    return attr


def charging_status_key(status: str) -> str:
    """Convert a charging status to a valid key."""
    if status == "chargeComplete":
        return "charge_complete"
    return status


def decode_remote_display(value: Any) -> str:  # noqa: ANN401
    """Decode a RemoteDisplayStatus value to its enum name.

    ``remote_display`` arrives as an int, a numeric string, or (rarely) an
    already-decoded string. ``7`` / ``ACTIVATED`` is the only state in which the
    car will actually act on a remote command; surfaced in diagnostics.
    """
    if isinstance(value, bool):  # bool is an int subclass — guard first
        return f"<non-status {value!r}>"
    if isinstance(value, int):
        return REMOTE_DISPLAY_NAMES.get(value, f"<unknown {value}>")
    if isinstance(value, str):
        if value.isdigit():
            return REMOTE_DISPLAY_NAMES.get(int(value), f"<unknown {value}>")
        return value
    if value is None:
        return "<missing>"
    return "<non-status, see raw>"


def vehicle_has_climate_capability(vehicle: Vehicle) -> bool:
    """Check whether a vehicle supports remote climate control.

    Mirrors pytoyoda's own ``Vehicle._climate_capable()`` gate for the
    climate_status/climate_settings endpoints: some vehicles (notably BEVs
    and certain PHEVs) advertise climate support only through
    ``extended_capabilities`` rather than through
    ``features.climate_start_engine`` (see issue #192). Vehicles that fail
    both checks never get a populated ``climate_status`` from pytoyoda, so
    entities gated on this always read as unknown/unavailable rather than
    just being created and never updating.
    """
    try:
        info = vehicle._vehicle_info  # noqa : SLF001
        if getattr(getattr(info, "features", False), "climate_start_engine", False):
            return True
        caps = getattr(info, "extended_capabilities", False)
        return any(
            getattr(caps, cap, False)
            for cap in (
                "climate_capable",
                "econnect_climate_capable",
                "remote_engine_start_stop",
            )
        )
    except Exception:  # pylint: disable=W0718 # noqa : BLE001
        return False


def predict_climate_class(features: Any, ext: Any) -> tuple[str, str]:  # noqa: ANN401
    """Predict a car's remote-climate archetype from its capability flags.

    Lets a diagnostics reader see which climate code path a car should follow
    without replaying endpoint calls. Returns ``(class, human_hint)``.
    """
    cse = getattr(features, "climate_start_engine", False)
    cc = getattr(ext, "climate_capable", False)
    ctf = getattr(ext, "climate_temperature_control_full", False)
    ctl = getattr(ext, "climate_temperature_control_limited", False)
    ecc = getattr(ext, "econnect_climate_capable", False)
    res = getattr(ext, "remote_engine_start_stop", False)

    has_temp_control = ctf or ctl
    if cc:
        return (
            ("FULL_CLIMATE", "target temp + on/off via V2 climate-control")
            if has_temp_control
            else ("CLIMATE_NO_TEMP", "on/off + defrost toggle; no target temp")
        )
    if res:
        return "ENGINE_PREHEAT", "engine-preheat on/off only; auto-off after ~20 min"
    if ecc:
        return "ECONNECT", "Stellantis-derived variant; treat like FULL_CLIMATE"
    if cse:
        return "LEGACY_FLAG", (
            "features.climate_start_engine only; behaviour depends on extended flags"
        )
    return "NO_CLIMATE", "no remote-climate flags set"
