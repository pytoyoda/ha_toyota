"""Regression tests for ha_toyota#322.

Toyota's registry has been observed reporting `fuel_level_available` /
`fuel_range_available` extended-capability flags as False for brand-new
model-year vehicles (e.g. RAV4 2026), even though the vehicle unambiguously
has a fuel tank, the underlying `telemetry` endpoint is already fetched
(gated only by `telemetry_capable`, proven working since `odometer` reads
correctly), and the MyToyota app shows the values correctly. The
`fuel_level`/`fuel_range` sensors were being hidden purely by this
possibly-wrong flag. This mirrors the existing false-negative widening
already applied to `_climate_capable()`/`_electric_capable()` upstream.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.toyota.sensor import create_sensor_configurations


class _Vehicle:
    """Minimal vehicle stub matching the fields sensor.py reads."""

    def __init__(
        self,
        *,
        type_: str = "fuel_only",
        fuel_level_available: bool = False,
        fuel_range_available: bool = False,
    ) -> None:
        self.type = type_
        self._vehicle_info = SimpleNamespace(
            extended_capabilities=SimpleNamespace(
                fuel_level_available=fuel_level_available,
                fuel_range_available=fuel_range_available,
            ),
        )


def _capability_check(key: str):
    configs = create_sensor_configurations(metric_values=True)
    return next(c for c in configs if c["description"].key == key)["capability_check"]


def test_fuel_level_shown_for_non_electric_even_when_flag_is_false():
    check = _capability_check("fuel_level")
    vehicle = _Vehicle(type_="fuel_only", fuel_level_available=False)

    assert check(vehicle) is True


def test_fuel_range_shown_for_non_electric_even_when_flag_is_false():
    check = _capability_check("fuel_range")
    vehicle = _Vehicle(type_="full_hybrid", fuel_range_available=False)

    assert check(vehicle) is True


def test_fuel_level_still_hidden_for_electric_when_flag_is_false():
    check = _capability_check("fuel_level")
    vehicle = _Vehicle(type_="electric", fuel_level_available=False)

    assert check(vehicle) is False


def test_fuel_range_still_hidden_for_electric_when_flag_is_false():
    check = _capability_check("fuel_range")
    vehicle = _Vehicle(type_="electric", fuel_range_available=False)

    assert check(vehicle) is False


def test_fuel_level_shown_for_electric_when_flag_is_true():
    """A PHEV misclassified oddly or reporting the flag true should still work."""
    check = _capability_check("fuel_level")
    vehicle = _Vehicle(type_="electric", fuel_level_available=True)

    assert check(vehicle) is True
