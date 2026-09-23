"""Tests for the pure helpers backing the extra Toyota sensors.

Focused on ``_cabin_temperature`` and ``_attr``: both fix real bugs where the
original implementation read data with the wrong access pattern and would
have silently reported a broken/empty state in production.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from homeassistant.const import UnitOfSpeed

from custom_components.toyota.sensor_extra import (
    ToyotaServiceDetailSensor,
    _attr,
    _cabin_temperature,
    _speed_unit,
)


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


def _make_service_detail_sensor(history) -> ToyotaServiceDetailSensor:
    """Build the entity without HA wiring; ``native_value`` only reads vehicle."""
    sensor = ToyotaServiceDetailSensor.__new__(ToyotaServiceDetailSensor)
    sensor.vehicle = SimpleNamespace(service_history=history)
    return sensor


def test_service_detail_reads_newest_record_first():
    """``service_history`` is newest-first, so the state is hist[0]'s date.

    Regression guard: reading hist[-1] reported the OLDEST record as the
    last-service date and disagreed with the sibling last_service sensor.
    """
    history = [
        SimpleNamespace(service_date=date(2026, 5, 20)),
        SimpleNamespace(service_date=date(2023, 6, 20)),
    ]
    sensor = _make_service_detail_sensor(history)
    assert sensor.native_value == "2026-05-20"


def test_service_detail_none_when_history_empty():
    assert _make_service_detail_sensor([]).native_value is None


def test_service_detail_none_when_history_missing():
    assert _make_service_detail_sensor(None).native_value is None
