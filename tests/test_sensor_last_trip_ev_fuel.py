"""Tests for the per-trip EV share and fuel consumption sensors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.toyota.sensor_extra import (
    CAPABILITY_CHECKS,
    ToyotaLastTripEvShareSensor,
    ToyotaLastTripFuelConsumptionSensor,
)

# A real Corolla Cross hybrid trip: 2585 m, 2128 m in EV mode, 78 ml of fuel.
_TRIP = {
    "start_ts": "2026-11-23T17:23:07Z",
    "end_ts": "2026-11-23T17:38:26Z",
    "stats": {
        "distance_m": 2585,
        "duration_s": 919,
        "ev_distance_m": 2128,
        "ev_time_s": 806,
        "fuel_consumption_ml": 78.0,
    },
}


def _make(cls, last_trip, *, metric=True):
    sensor = cls.__new__(cls)
    sensor._last_trip = lambda: last_trip  # type: ignore[method-assign]
    sensor.metric_values = metric
    return sensor


def test_ev_share_is_ev_distance_over_trip_distance():
    sensor = _make(ToyotaLastTripEvShareSensor, _TRIP)
    assert sensor.native_value == 82.3
    attrs = sensor.extra_state_attributes
    assert attrs["ev_distance_m"] == 2128
    assert attrs["ev_time_s"] == 806


@pytest.mark.parametrize(
    "trip",
    [
        None,
        {"stats": {"distance_m": 1000}},
        {"stats": {"distance_m": 0, "ev_distance_m": 0}},
        {"stats": {"ev_distance_m": 100}},
    ],
)
def test_ev_share_unknown_without_data(trip):
    assert _make(ToyotaLastTripEvShareSensor, trip).native_value is None


def test_ev_share_capped_at_100():
    trip = {"stats": {"distance_m": 1000, "ev_distance_m": 1010}}
    assert _make(ToyotaLastTripEvShareSensor, trip).native_value == 100.0


def test_fuel_consumption_metric():
    sensor = _make(ToyotaLastTripFuelConsumptionSensor, _TRIP)
    assert sensor.native_value == 3.02
    assert sensor.native_unit_of_measurement == "L/100km"
    assert sensor.extra_state_attributes["fuel_used_l"] == 0.078


def test_fuel_consumption_imperial():
    sensor = _make(ToyotaLastTripFuelConsumptionSensor, _TRIP, metric=False)
    assert sensor.native_unit_of_measurement == "MPG"
    assert sensor.native_value == pytest.approx(78.0, abs=0.1)


def test_fuel_consumption_unknown_without_data():
    trip = {"stats": {"distance_m": 1000}}
    assert _make(ToyotaLastTripFuelConsumptionSensor, trip).native_value is None
    assert _make(ToyotaLastTripFuelConsumptionSensor, None).native_value is None


def _vehicle(fuel_type="B", **caps):
    return SimpleNamespace(
        _vehicle_info=SimpleNamespace(
            fuel_type=fuel_type, extended_capabilities=SimpleNamespace(**caps)
        )
    )


def test_ev_share_only_for_hybrids():
    check = CAPABILITY_CHECKS["last_trip_ev_share"]
    assert check(_vehicle(hybrid_pulse=True))
    assert check(_vehicle(econnect_vehicle_status_capable=True))
    assert not check(_vehicle(hybrid_pulse=False))


def test_fuel_consumption_only_with_a_fuel_type():
    check = CAPABILITY_CHECKS["last_trip_fuel_consumption"]
    assert check(_vehicle("B"))
    assert not check(_vehicle(None))
