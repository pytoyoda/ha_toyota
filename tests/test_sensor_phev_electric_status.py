"""Regression tests for ha_toyota#434.

Plug-in hybrids (`type == "plug_in_hybrid"`) can report the same
`electric_status` data as full EVs (battery level/range, charging status,
remaining charge time) whenever `econnect_vehicle_status_capable` is
missing/false. The battery/charging sensor capability checks only fell back
to `v.type == "electric"`, so PHEVs never got these entities even though
valid data was available.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.toyota.sensor import create_sensor_configurations

_ELECTRIC_STATUS_SENSOR_KEYS = (
    "battery_level",
    "battery_range",
    "battery_range_ac",
    "charging_status",
    "remaining_charge_time",
)


class _Vehicle:
    """Minimal vehicle stub matching the fields sensor.py reads."""

    def __init__(
        self,
        *,
        type_: str,
        econnect_vehicle_status_capable: bool = False,
    ) -> None:
        self.type = type_
        self._vehicle_info = SimpleNamespace(
            extended_capabilities=SimpleNamespace(
                econnect_vehicle_status_capable=econnect_vehicle_status_capable,
            ),
        )


def _capability_check(key: str):
    configs = create_sensor_configurations(metric_values=True)
    return next(c for c in configs if c["description"].key == key)["capability_check"]


def test_electric_status_sensors_shown_for_plug_in_hybrid_without_flag():
    vehicle = _Vehicle(type_="plug_in_hybrid", econnect_vehicle_status_capable=False)

    for key in _ELECTRIC_STATUS_SENSOR_KEYS:
        assert _capability_check(key)(vehicle) is True, key


def test_electric_status_sensors_still_shown_for_electric_without_flag():
    vehicle = _Vehicle(type_="electric", econnect_vehicle_status_capable=False)

    for key in _ELECTRIC_STATUS_SENSOR_KEYS:
        assert _capability_check(key)(vehicle) is True, key


def test_electric_status_sensors_hidden_for_fuel_only_without_flag():
    vehicle = _Vehicle(type_="fuel_only", econnect_vehicle_status_capable=False)

    for key in _ELECTRIC_STATUS_SENSOR_KEYS:
        assert _capability_check(key)(vehicle) is False, key


def test_electric_status_sensors_shown_for_full_hybrid_when_flag_is_true():
    vehicle = _Vehicle(type_="full_hybrid", econnect_vehicle_status_capable=True)

    for key in _ELECTRIC_STATUS_SENSOR_KEYS:
        assert _capability_check(key)(vehicle) is True, key
