"""Unit tests for the toyota.start_climate service (issue #424).

Toyota enforces a strict per-ignition-cycle quota on remote climate starts
(2 starts / 20 cumulative minutes between two READY-mode cycles). Setting
the steering heater, seat heaters, defrost and temperature one entity at a
time each dispatches its own start command and can exhaust that quota
within seconds. ``toyota.start_climate`` batches every provided field into
a single ``async_apply_climate_settings`` call per vehicle so only one
remote-start unit is consumed.

These tests cover the pure/near-pure helpers extracted from
_async_register_services() in __init__.py (``_find_vehicle_by_vin``,
``_onoff_field``) plus the extended ``async_apply_climate_settings``
overrides added for this service (front_defroster, rear_defogger,
temperature, duration_minutes), following the pattern already used by
test_refresh_electric_realtime_status.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytoyoda.models.endpoints.climate import (
    ClimateSettingsResponseModel,
    RemoteClimateControlResponseModel,
)

from custom_components.toyota import _find_vehicle_by_vin, _onoff_field
from custom_components.toyota.climate import async_apply_climate_settings


class _FakeCall:
    """Minimal stand-in for homeassistant.core.ServiceCall."""

    def __init__(self, data: dict) -> None:
        self.data = data


# --- _find_vehicle_by_vin ----------------------------------------------------


def test_find_vehicle_by_vin_returns_matching_vehicle() -> None:
    vehicle = SimpleNamespace(vin="VIN123")
    vehicle_data = [{"data": vehicle}]

    assert _find_vehicle_by_vin(vehicle_data, "VIN123") is vehicle


def test_find_vehicle_by_vin_returns_none_for_unknown_vin() -> None:
    vehicle = SimpleNamespace(vin="VIN123")
    vehicle_data = [{"data": vehicle}]

    assert _find_vehicle_by_vin(vehicle_data, "OTHER-VIN") is None


def test_find_vehicle_by_vin_ignores_entries_without_data() -> None:
    vehicle_data = [{"data": None}, SimpleNamespace(get=lambda *_a: None)]

    assert _find_vehicle_by_vin(vehicle_data, "VIN123") is None


# --- _onoff_field --------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"steering_heater": True}, "on"),
        ({"steering_heater": False}, "off"),
        ({}, None),
        ({"steering_heater": None}, None),
    ],
)
def test_onoff_field_converts_bool_to_wire_string(
    data: dict, expected: str | None
) -> None:
    assert _onoff_field(_FakeCall(data), "steering_heater") == expected


# --- async_apply_climate_settings batched overrides ---------------------------


def _climate_settings(
    *,
    front_defroster: str | None = "off",
    rear_defogger: str | None = "off",
    steering_heater: str | None = "off",
    driver_seat: str | None = "off",
    temperature: float | None = 22,
) -> ClimateSettingsResponseModel:
    return ClimateSettingsResponseModel.model_validate(
        {
            "payload": {
                "duration": 15,
                "temperature": (
                    {"unit": "C", "value": temperature}
                    if temperature is not None
                    else None
                ),
                "heatingOptions": {
                    "frontDefroster": front_defroster,
                    "rearDefogger": rear_defogger,
                    "steeringHeater": steering_heater,
                },
                "seatOptions": {"driverSeat": driver_seat},
            }
        }
    )


class _Vehicle:
    """Minimal vehicle shaped like pytoyoda's public climate interfaces."""

    def __init__(self, *, climate_settings: ClimateSettingsResponseModel) -> None:
        from unittest.mock import AsyncMock

        self.vin = "JTDBR32E720000001"
        self._endpoint_data = {"climate_settings": climate_settings}
        self.set_climate = AsyncMock(
            return_value=RemoteClimateControlResponseModel.model_validate(
                {"payload": {"appRequestNo": "1", "returnCode": "000000"}}
            )
        )

    @property
    def climate_settings(self):
        from pytoyoda.models.climate import ClimateSettings

        return ClimateSettings(self._endpoint_data["climate_settings"])


@pytest.mark.asyncio
async def test_apply_settings_batches_all_overrides_into_one_request() -> None:
    """A single call combining every override sends exactly one start command."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(
            front_defroster="off",
            rear_defogger="off",
            steering_heater="off",
            driver_seat="off",
            temperature=18,
        )
    )

    await async_apply_climate_settings(
        vehicle,
        steering_heater="on",
        front_defroster="on",
        rear_defogger="on",
        seat_overrides={"driver_seat": "high"},
        temperature=24,
        duration_minutes=10,
    )

    assert vehicle.set_climate.call_count == 1
    request = vehicle.set_climate.call_args.args[0]
    assert request.heating_options.steering_heater == "on"
    assert request.heating_options.front_defroster == "on"
    assert request.heating_options.rear_defogger == "on"
    assert request.seat_options.driver_seat == "heater"
    assert request.temperature.value == 24
    assert request.duration == 10


@pytest.mark.asyncio
async def test_apply_settings_echoes_unset_overrides() -> None:
    """Fields not passed keep the vehicle's last-read value, not None."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(
            front_defroster="on",
            rear_defogger="off",
            steering_heater="off",
            temperature=21,
        )
    )

    await async_apply_climate_settings(vehicle, steering_heater="on")

    request = vehicle.set_climate.call_args.args[0]
    assert request.heating_options.front_defroster == "on"
    assert request.heating_options.rear_defogger == "off"
    assert request.temperature.value == 21
    assert request.duration is None
