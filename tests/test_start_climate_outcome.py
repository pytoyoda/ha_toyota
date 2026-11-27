"""The outcome of a climate start is reported to the caller and recorded.

``toyota.start_climate`` used to log and swallow Toyota's rejection, so a
script, automation or dashboard button calling it always "succeeded" even when
no climate started. It was also the only start path that left nothing in the
diagnostics' ``last_command_result_per_vin``. The seat-heater selects and the
steering-heater switch share the same start helper and were unrecorded too.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.toyota import _async_register_climate_services
from custom_components.toyota.climate import async_apply_climate_settings
from custom_components.toyota.const import DOMAIN
from tests.test_seat_steering_controls import (
    _climate_settings,
    _coordinator_with,
    _rejected_response,
    _select_entity,
    _switch_entity,
    _Vehicle,
)

VIN_A = "JTDBR32E720000001"
VIN_B = "JTDBR32E720000002"


def _vehicle(vin: str = VIN_A, *, rejected: bool = False) -> _Vehicle:
    vehicle = _Vehicle(
        climate_settings=_climate_settings(),
        set_climate_response=_rejected_response() if rejected else None,
    )
    vehicle.vin = vin
    return vehicle


def _results(hass) -> dict:
    return hass.data[DOMAIN]["entry-id_diag"].get("last_command_result_per_vin", {})


# --- async_apply_climate_settings ---------------------------------------------


async def test_accepted_start_is_recorded(hass) -> None:
    """An accepted start is stored with Toyota's return code."""
    hass.data[DOMAIN] = {"entry-id_diag": {}}
    await async_apply_climate_settings(
        _vehicle(), steering_heater="on", hass=hass, entry_id="entry-id"
    )
    result = _results(hass)[VIN_A]
    assert result["command"] == "climate_start"
    assert result["ok"] is True
    assert result["code"] == "000000"


async def test_rejected_start_is_recorded_and_raised(hass) -> None:
    """A rejection is stored with its code and still raises."""
    hass.data[DOMAIN] = {"entry-id_diag": {}}
    with pytest.raises(HomeAssistantError):
        await async_apply_climate_settings(
            _vehicle(rejected=True), hass=hass, entry_id="entry-id"
        )
    result = _results(hass)[VIN_A]
    assert result["ok"] is False
    assert result["code"] == "118003"


async def test_transport_failure_is_recorded_and_raised(hass) -> None:
    """A send that never got an answer is stored by error type only."""
    hass.data[DOMAIN] = {"entry-id_diag": {}}
    vehicle = _vehicle()
    vehicle.set_climate.side_effect = httpx.ReadTimeout("timed out")
    with pytest.raises(httpx.ReadTimeout):
        await async_apply_climate_settings(vehicle, hass=hass, entry_id="entry-id")
    result = _results(hass)[VIN_A]
    assert result["ok"] is False
    assert result["detail"] == "ReadTimeout"


async def test_start_without_hass_records_nothing(hass) -> None:
    """Callers that pass no hass/entry keep the old, unrecorded behaviour."""
    hass.data[DOMAIN] = {"entry-id_diag": {}}
    await async_apply_climate_settings(_vehicle(), steering_heater="on")
    assert _results(hass) == {}


# --- seat-heater select and steering-heater switch ----------------------------


async def test_seat_heater_start_is_recorded(hass) -> None:
    """A seat-heater change is a climate start and is recorded as one."""
    entity = _select_entity(hass, _vehicle(), "driver_seat")
    hass.data[DOMAIN]["entry-id_diag"] = {}
    await entity.async_select_option("high")
    assert _results(hass)[VIN_A]["command"] == "climate_start"


async def test_steering_heater_start_is_recorded(hass) -> None:
    """A steering-heater change is a climate start and is recorded as one."""
    entity = _switch_entity(hass, _vehicle(rejected=True))
    hass.data[DOMAIN]["entry-id_diag"] = {}
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert _results(hass)[VIN_A]["ok"] is False


# --- toyota.start_climate service ---------------------------------------------


async def _call_service(
    hass, vehicles: list[_Vehicle], *, targets: dict | None = None
) -> None:
    """Call toyota.start_climate for the given vehicles of one entry."""
    first, *rest = vehicles
    _entry, coordinator = _coordinator_with(hass, first)
    coordinator.data += [{"data": v} for v in rest]
    coordinator.async_request_refresh = AsyncMock()
    hass.data[DOMAIN]["entry-id_diag"] = {}
    await _async_register_climate_services(hass)
    if targets is None:
        targets = {"entry-id": [v.vin for v in vehicles]}
    with patch(
        "custom_components.toyota._resolve_devices_to_vins_per_entry",
        return_value=targets,
    ):
        await hass.services.async_call(
            DOMAIN,
            "start_climate",
            {"device_id": "device", "steering_heater": True},
            blocking=True,
        )


async def test_service_succeeds_when_toyota_accepts(hass) -> None:
    """An accepted start returns normally and is recorded."""
    vehicle = _vehicle()
    await _call_service(hass, [vehicle])
    assert vehicle.set_climate.call_count == 1
    assert _results(hass)[VIN_A]["ok"] is True


async def test_service_raises_when_toyota_rejects(hass) -> None:
    """Regression: the rejection reaches the caller instead of being swallowed."""
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await _call_service(hass, [_vehicle(rejected=True)])
    assert _results(hass)[VIN_A]["ok"] is False


async def test_service_raises_on_transport_failure(hass) -> None:
    """A start that got no answer is reported as a failure, not a success."""
    vehicle = _vehicle()
    vehicle.set_climate.side_effect = httpx.ReadTimeout("timed out")
    with pytest.raises(HomeAssistantError, match="ReadTimeout"):
        await _call_service(hass, [vehicle])


async def test_service_tries_every_vehicle_before_raising(hass) -> None:
    """One car's rejection neither skips the next car nor hides the failure."""
    rejected, accepted = _vehicle(VIN_A, rejected=True), _vehicle(VIN_B)
    with pytest.raises(HomeAssistantError):
        await _call_service(hass, [rejected, accepted])
    assert rejected.set_climate.call_count == 1
    assert accepted.set_climate.call_count == 1
    results = _results(hass)
    assert results[VIN_A]["ok"] is False
    assert results[VIN_B]["ok"] is True


async def test_service_names_the_vehicle_that_failed(hass) -> None:
    """With several cars, the error says which one did not start."""
    rejected = _vehicle(VIN_A, rejected=True)
    rejected.alias = "Corolla"
    with pytest.raises(HomeAssistantError, match="^Corolla: Toyota did not accept"):
        await _call_service(hass, [rejected, _vehicle(VIN_B)])


async def test_service_raises_when_no_vehicle_is_loaded(hass) -> None:
    """A target whose entry is not loaded must not report success."""
    vehicle = _vehicle()
    with pytest.raises(HomeAssistantError, match="not available"):
        await _call_service(hass, [vehicle], targets={})
    vehicle.set_climate.assert_not_called()


async def test_service_raises_when_vehicle_is_missing_from_data(hass) -> None:
    """A VIN the coordinator has no data for is a failure, not a skip."""
    with pytest.raises(HomeAssistantError, match="not available"):
        await _call_service(hass, [_vehicle()], targets={"entry-id": [VIN_B]})
