"""Tests for ToyotaCoordinatorStateSensor, in particular
last_refresh_error_code's "no error yet" state.

No prior coverage existed for these diagnostic sensors (last_successful_
fetch, last_refresh_error_time, last_refresh_error_code,
status_last_reported, status_refresh_state). This focuses on the
last_refresh_error_code "none" sentinel added because a raw ``None`` state
always renders as the ambiguous "Unknown" in the HA frontend - and on the
fact that last_refresh_error_time, being a device_class=TIMESTAMP sensor,
has no equivalent sentinel available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.sensor import (
    LAST_REFRESH_ERROR_CODE_ENTITY_DESCRIPTION,
    LAST_REFRESH_ERROR_TIME_ENTITY_DESCRIPTION,
    NO_ERROR_CODE,
    STATUS_LAST_REPORTED_ENTITY_DESCRIPTION,
    ToyotaCoordinatorStateSensor,
)


def _vehicle(vin: str = "SB1TESTVIN00000004") -> SimpleNamespace:
    return SimpleNamespace(
        vin=vin,
        alias="Test car",
        _vehicle_info=SimpleNamespace(brand="T", car_model_name="Test"),
    )


def _entity(hass, description, vehicle) -> ToyotaCoordinatorStateSensor:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [{"data": vehicle, "statistics": None, "metric_values": True}]
    return ToyotaCoordinatorStateSensor(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=description,
    )


def test_last_refresh_error_code_reads_none_sentinel_when_no_error(hass) -> None:
    """No entry in _diag_last_error_per_vin yet -> "none", not raw None."""
    vehicle = _vehicle()
    entity = _entity(hass, LAST_REFRESH_ERROR_CODE_ENTITY_DESCRIPTION, vehicle)
    entity.coordinator._diag_last_error_per_vin = {}

    assert entity.native_value == NO_ERROR_CODE


def test_last_refresh_error_time_stays_none_when_no_error(hass) -> None:
    """No sentinel here - stays None (renders "Unknown" in HA)."""
    vehicle = _vehicle()
    entity = _entity(hass, LAST_REFRESH_ERROR_TIME_ENTITY_DESCRIPTION, vehicle)
    entity.coordinator._diag_last_error_per_vin = {}

    assert entity.native_value is None


def test_last_refresh_error_code_reads_recorded_code(hass) -> None:
    """Once an error is recorded, the real code wins over the sentinel."""
    vehicle = _vehicle()
    entity = _entity(hass, LAST_REFRESH_ERROR_CODE_ENTITY_DESCRIPTION, vehicle)
    entity.coordinator._diag_last_error_per_vin = {
        vehicle.vin: (datetime(2026, 1, 1, tzinfo=timezone.utc), "HTTP 429")
    }

    assert entity.native_value == "HTTP 429"


def test_diagnostic_sensor_stays_available_with_no_data_yet(hass) -> None:
    """Diagnostic sensors must never go unavailable, even with an empty dict."""
    vehicle = _vehicle()
    entity = _entity(hass, STATUS_LAST_REPORTED_ENTITY_DESCRIPTION, vehicle)
    entity.coordinator._diag_status_occurrence_per_vin = {}

    assert entity.available is True
    assert entity.native_value is None
