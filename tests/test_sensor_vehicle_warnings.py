"""Tests for the vehicle warning sensors (warning_lights / warning_message)."""

from types import SimpleNamespace

from custom_components.toyota.sensor_extra import (
    _active_warnings,
    ToyotaWarningLightsSensor,
    ToyotaWarningMessageSensor,
)

# Shape returned by Toyota's health-status endpoint (low tire pressure).
TIRE_WARNING = {
    "wngcode": "TILW",
    "wngtype": "S",
    "wngdesc": "Tire low pressure warning",
    "wngicon": [
        {"deviceName": "ipad", "deviceUrl": "https://example.invalid/TILW.png"},
        {"deviceName": "android", "deviceUrl": "https://example.invalid/b.png"},
    ],
    "wngdcmtime": "2026-11-22T20:34:58Z",
    "wngfilterType": "Maintenance",
    "cta": ["bookService", "contactDealer", "contactRsa"],
    "severity": "4",
}


def _vehicle(warnings):
    dash = None if warnings is None else SimpleNamespace(warning_lights=warnings)
    return SimpleNamespace(dashboard=dash)


def _sensor(cls, warnings):
    sensor = cls.__new__(cls)
    sensor.vehicle = _vehicle(warnings)
    return sensor


def test_health_status_entry_is_parsed():
    """Regression: entries were read as name/status/category, which Toyota
    never sends, so the count was 0 and every attribute was null.
    """
    assert _active_warnings(_vehicle([TIRE_WARNING])) == [
        {
            "code": "TILW",
            "description": "Tire low pressure warning",
            "type": "S",
            "category": "Maintenance",
            "severity": "4",
            "reported_at": "2026-11-22T20:34:58Z",
            "icon_url": "https://example.invalid/TILW.png",
        }
    ]


def test_warning_lights_counts_health_warnings():
    sensor = _sensor(ToyotaWarningLightsSensor, [TIRE_WARNING])
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["warnings"][0]["code"] == "TILW"


def test_warning_lights_zero_when_no_warnings():
    sensor = _sensor(ToyotaWarningLightsSensor, [])
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes is None


def test_warning_lights_unknown_before_health_is_reported():
    assert _sensor(ToyotaWarningLightsSensor, None).native_value is None


def test_legacy_status_entries_still_respect_off():
    entries = [
        {"name": "Oil", "status": "on", "category": "engine"},
        {"name": "ABS", "status": "off", "category": "brakes"},
    ]
    warnings = _active_warnings(_vehicle(entries))
    assert [w["description"] for w in warnings] == ["Oil"]
    assert warnings[0]["category"] == "engine"


def test_warning_message_shows_description():
    sensor = _sensor(ToyotaWarningMessageSensor, [TIRE_WARNING])
    assert sensor.native_value == "Tire low pressure warning"
    assert sensor.extra_state_attributes == {"codes": ["TILW"]}


def test_warning_message_joins_several_warnings():
    other = {"wngcode": "OILW", "wngdesc": "Engine oil level low"}
    sensor = _sensor(ToyotaWarningMessageSensor, [TIRE_WARNING, other])
    assert (
        sensor.native_value == "Tire low pressure warning, Engine oil level low"
    )


def test_warning_message_falls_back_to_code():
    sensor = _sensor(ToyotaWarningMessageSensor, [{"wngcode": "XYZ"}])
    assert sensor.native_value == "XYZ"


def test_warning_message_ok_when_no_warnings():
    assert _sensor(ToyotaWarningMessageSensor, []).native_value == "OK"


def test_warning_message_unknown_before_health_is_reported():
    assert _sensor(ToyotaWarningMessageSensor, None).native_value is None


def test_warning_message_fits_state_length_limit():
    many = [{"wngcode": str(i), "wngdesc": "x" * 60} for i in range(10)]
    assert len(_sensor(ToyotaWarningMessageSensor, many).native_value) == 255
