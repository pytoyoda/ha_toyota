"""Command recording and its public diagnostics export boundary."""

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.toyota.utils import record_command_result

VIN_A = "JTDBR32E720000001"
VIN_B = "JTDBR32E720000002"


@pytest.mark.parametrize("data", [{}, {DOMAIN: {}}])
def test_missing_diagnostics_state_is_harmless(hass, data):
    """An absent bucket must not create state or break the command path."""
    hass.data.update(data)
    record_command_result(hass, "entry", VIN_A, "DOOR_LOCK", ok=True)
    assert "entry_diag" not in hass.data.get(DOMAIN, {})


def test_latest_result_is_isolated_by_entry_and_vehicle(hass):
    """A new command replaces only that car's last result."""
    first, second = {}, {}
    hass.data[DOMAIN] = {"first_diag": first, "second_diag": second}
    record_command_result(hass, "first", VIN_A, "DOOR_LOCK", ok=True)
    record_command_result(hass, "first", VIN_B, "climate_start", ok=True)
    record_command_result(hass, "second", VIN_A, "DOOR_UNLOCK", ok=True)
    record_command_result(hass, "first", None, "DOOR_LOCK", ok=False)
    record_command_result(hass, "first", VIN_A, "climate_stop", ok=False, code="118003")
    results = first["last_command_result_per_vin"]
    assert set(results) == {VIN_A, VIN_B}
    assert results[VIN_A]["command"] == "climate_stop"
    assert results[VIN_A]["ok"] is False
    assert results[VIN_B]["command"] == "climate_start"
    assert second["last_command_result_per_vin"][VIN_A]["command"] == "DOOR_UNLOCK"


async def test_download_scopes_and_redacts_recorded_results(hass):
    """The actual download entry points export records without raw VINs."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry")
    vehicles = [SimpleNamespace(vin=vin) for vin in (VIN_A, VIN_B)]
    coordinator = SimpleNamespace(data=[{"data": v} for v in vehicles])
    hass.data[DOMAIN] = {entry.entry_id: coordinator, "entry_diag": {}}
    record_command_result(hass, "entry", VIN_A, "DOOR_LOCK", ok=True, code=202)
    record_command_result(hass, "entry", VIN_B, "climate_stop", ok=False, code="118003")

    all_data = await async_get_config_entry_diagnostics(hass, entry)
    device_data = await async_get_device_diagnostics(
        hass, entry, SimpleNamespace(identifiers={(DOMAIN, VIN_A)}, name="Car", model="Test")
    )
    for exported in (all_data, device_data):
        assert VIN_A not in str(exported)
        assert VIN_B not in str(exported)
    all_results = all_data["diagnostics_state"]["last_command_result_per_vin"]
    device_results = device_data["diagnostics_state"]["last_command_result_per_vin"]
    assert len(all_results) == 2
    assert len(device_results) == 1
    result = next(iter(device_results.values()))
    assert result["command"] == "DOOR_LOCK"
    assert result["ok"] is True
    assert result["code"] == 202
