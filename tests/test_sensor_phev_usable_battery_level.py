"""Regression tests for ha_toyota#437.

pytoyoda 5.2.9 added `electric_status.phev_usable_battery_level` as an
optional field distinct from `dashboard.battery_level`: for plug-in
hybrids the latter may report a higher value that isn't representative of
the battery actually usable for EV driving. This adds a dedicated sensor
for PHEVs only, sourced from `electric_status` (not `dashboard`).
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.toyota.sensor import create_sensor_configurations

_KEY = "phev_usable_battery_level"


def _get_config():
    configs = create_sensor_configurations(metric_values=True)
    return next(c for c in configs if c["description"].key == _KEY)


def test_capability_check_true_only_for_plug_in_hybrid():
    config = _get_config()

    assert config["capability_check"](SimpleNamespace(type="plug_in_hybrid")) is True
    assert config["capability_check"](SimpleNamespace(type="electric")) is False
    assert config["capability_check"](SimpleNamespace(type="fuel_only")) is False
    assert config["capability_check"](SimpleNamespace(type="full_hybrid")) is False


def test_value_fn_reads_from_electric_status():
    config = _get_config()
    vehicle = SimpleNamespace(
        electric_status=SimpleNamespace(phev_usable_battery_level=87.4)
    )

    assert config["description"].value_fn(vehicle) == 87


def test_value_fn_none_when_electric_status_missing():
    config = _get_config()
    vehicle = SimpleNamespace(electric_status=None)

    assert config["description"].value_fn(vehicle) is None


def test_value_fn_none_when_field_not_reported():
    config = _get_config()
    vehicle = SimpleNamespace(
        electric_status=SimpleNamespace(phev_usable_battery_level=None)
    )

    assert config["description"].value_fn(vehicle) is None
