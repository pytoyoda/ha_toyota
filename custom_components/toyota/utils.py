"""Utilities for Toyota integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_BRAND_MAPPING

if TYPE_CHECKING:
    from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
    from pytoyoda.models.summary import Summary


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
