"""Entities must resolve their vehicle by VIN, not by position in coordinator.data.

coordinator.data mirrors the order in which the Toyota API returns the
account's vehicles. That order is not stable, so an entity bound to a VIN at
setup must keep reading that VIN's data when the list is reordered or shrinks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.entity import ToyotaBaseEntity

VIN_A = "SB1TESTVIN0000000A"
VIN_B = "SB1TESTVIN0000000B"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _vehicle_data(vin: str, *, fresh: bool = True) -> dict:
    return {
        "data": SimpleNamespace(
            vin=vin,
            alias=f"Car {vin[-1]}",
            _vehicle_info=SimpleNamespace(brand="T", car_model_name="Test"),
        ),
        "statistics": f"stats-{vin[-1]}",
        "metric_values": vin == VIN_A,
        "is_cached": False,
        "last_successful_fetch": NOW if fresh else None,
    }


def _coordinator(hass, data: list[dict]) -> DataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = data
    return coordinator


def _entity(hass, coordinator: DataUpdateCoordinator, index: int) -> ToyotaBaseEntity:
    entity = ToyotaBaseEntity(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=index,
        description=EntityDescription(key="probe"),
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


def test_swapped_order_keeps_each_entity_on_its_own_vehicle(hass) -> None:
    """The API returning [B, A] instead of [A, B] must not swap the data."""
    a, b = _vehicle_data(VIN_A), _vehicle_data(VIN_B)
    coordinator = _coordinator(hass, [a, b])
    entity_a = _entity(hass, coordinator, 0)
    entity_b = _entity(hass, coordinator, 1)

    coordinator.data = [b, a]
    entity_a._handle_coordinator_update()
    entity_b._handle_coordinator_update()

    assert entity_a.vehicle.vin == VIN_A
    assert entity_a.statistics == "stats-A"
    assert entity_a.metric_values is True
    assert entity_b.vehicle.vin == VIN_B
    assert entity_b.statistics == "stats-B"
    assert entity_b.metric_values is False
    assert entity_a.unique_id == f"entry-id_{VIN_A}/probe"


def test_update_picks_up_fresh_objects_for_the_same_vin(hass) -> None:
    """A new Vehicle object per cycle is still found by VIN, in any position."""
    coordinator = _coordinator(hass, [_vehicle_data(VIN_A), _vehicle_data(VIN_B)])
    entity_a = _entity(hass, coordinator, 0)

    refreshed_a = _vehicle_data(VIN_A)
    coordinator.data = [_vehicle_data(VIN_B), refreshed_a]
    entity_a._handle_coordinator_update()

    assert entity_a.vehicle is refreshed_a["data"]


def test_vehicle_dropping_out_does_not_shift_data_onto_a_sibling(hass) -> None:
    """With A missing from the cycle, B moves to index 0 but A must not adopt it."""
    a, b = _vehicle_data(VIN_A), _vehicle_data(VIN_B)
    coordinator = _coordinator(hass, [a, b])
    entity_a = _entity(hass, coordinator, 0)
    entity_b = _entity(hass, coordinator, 1)

    coordinator.data = [b]
    entity_a._handle_coordinator_update()
    entity_b._handle_coordinator_update()

    assert entity_a.vehicle.vin == VIN_A
    assert entity_a.available is False
    assert entity_b.vehicle.vin == VIN_B
    assert entity_b.available is True


def test_available_follows_own_vehicle_when_order_changes(hass) -> None:
    """Per-vehicle fault isolation must survive a reorder."""
    a = _vehicle_data(VIN_A)
    b_stub = _vehicle_data(VIN_B, fresh=False)
    coordinator = _coordinator(hass, [a, b_stub])
    entity_a = _entity(hass, coordinator, 0)
    entity_b = _entity(hass, coordinator, 1)

    coordinator.data = [b_stub, a]

    assert entity_a.available is True
    assert entity_b.available is False


def test_empty_coordinator_data_is_unavailable_not_an_error(hass) -> None:
    """An empty/None data payload must not raise from available or update."""
    coordinator = _coordinator(hass, [_vehicle_data(VIN_A)])
    entity_a = _entity(hass, coordinator, 0)

    for payload in ([], None):
        coordinator.data = payload
        entity_a._handle_coordinator_update()
        assert entity_a.available is False
        assert entity_a.vehicle.vin == VIN_A
