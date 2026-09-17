"""Tests for the pure helpers backing the extra Toyota sensors.

Focused on ``_cabin_temperature`` and ``_attr``: both fix real bugs where the
original implementation read data with the wrong access pattern and would
have silently reported a broken/empty state in production.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.const import UnitOfSpeed

from custom_components.toyota.sensor_extra import _attr, _cabin_temperature, _speed_unit


class _UnitValue:
    """Stand-in for pytoyoda's ``UnitValueModel`` (``value`` + ``unit``)."""

    def __init__(self, value):
        self.value = value


def test_cabin_temperature_unwraps_unit_value_model():
    """climate_status.current_temperature is a UnitValueModel, not a float.

    Regression guard: returning the model itself (instead of ``.value``)
    would surface as a broken/non-numeric sensor state in Home Assistant.
    """
    vehicle = SimpleNamespace(
        climate_status=SimpleNamespace(current_temperature=_UnitValue(21.5))
    )
    assert _cabin_temperature(vehicle) == 21.5


def test_cabin_temperature_none_when_current_temperature_missing():
    vehicle = SimpleNamespace(climate_status=SimpleNamespace(current_temperature=None))
    assert _cabin_temperature(vehicle) is None


def test_cabin_temperature_none_when_climate_status_missing():
    vehicle = SimpleNamespace(climate_status=None)
    assert _cabin_temperature(vehicle) is None


def test_cabin_temperature_none_when_vehicle_lacks_climate_status():
    vehicle = SimpleNamespace()
    assert _cabin_temperature(vehicle) is None


def test_attr_reads_dict_items():
    """Dashboard.warning_lights is typed list[Any] - raw JSON deserialises
    to plain dicts, not attribute-bearing models.
    """
    assert _attr({"status": "on"}, "status") == "on"
    assert _attr({"status": "on"}, "missing", "default") == "default"


def test_attr_reads_object_attributes():
    obj = SimpleNamespace(status="on")
    assert _attr(obj, "status") == "on"
    assert _attr(obj, "missing", "default") == "default"


def test_speed_unit_metric_is_kmh():
    """Summary.average_speed already reports in the account's configured unit.

    Regression guard: hardcoding km/h regardless of metric_values would
    mislabel mph values as km/h for imperial accounts.
    """
    assert _speed_unit(metric_values=True) == UnitOfSpeed.KILOMETERS_PER_HOUR


def test_speed_unit_imperial_is_mph():
    assert _speed_unit(metric_values=False) == UnitOfSpeed.MILES_PER_HOUR
