"""Regression tests for the climate entity's ``current_temperature`` (issue #363).

The Toyota EU API can report the vehicle's measured cabin/interior temperature
via ``ClimateStatus.current_temperature``. ``ToyotaClimate`` must surface that
same value as its own ``current_temperature`` property (like any other HA
thermostat), and must fall back to ``None`` - not a crash or a bogus 0 - when
the vehicle doesn't report it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.climate import ClimateStatus
from pytoyoda.models.endpoints.climate import ClimateStatusResponseModel

from custom_components.toyota.climate import ToyotaClimate
from custom_components.toyota.const import DOMAIN


def _climate_status(*, current_temperature: float | None) -> ClimateStatusResponseModel:
    """Build a real climate-status response, exercising pytoyoda's own models."""
    return ClimateStatusResponseModel.model_validate(
        {
            "payload": {
                "currentTemperature": (
                    {"unit": "C", "value": current_temperature}
                    if current_temperature is not None
                    else None
                ),
            }
        }
    )


class _Vehicle:
    """Minimal vehicle shaped like pytoyoda's public climate interfaces."""

    def __init__(
        self, *, climate_status: ClimateStatusResponseModel | None = None
    ) -> None:
        self.vin = "JTDBR32E720000001"
        self.alias = "Test car"
        self._vehicle_info = SimpleNamespace(
            brand="T",
            car_model_name="Test",
            features=SimpleNamespace(climate_start_engine=True),
            extended_capabilities=SimpleNamespace(
                climate_capable=True,
                econnect_climate_capable=True,
                remote_engine_start_stop=False,
            ),
        )
        self.climate_settings = None
        self._climate_status_raw = climate_status

    @property
    def climate_status(self) -> ClimateStatus | None:
        raw = self._climate_status_raw
        return ClimateStatus(raw) if raw is not None else None


def _coordinator_with(hass, vehicle: _Vehicle):
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [{"data": vehicle, "statistics": None, "metric_values": True}]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


def _climate_entity(hass, vehicle: _Vehicle) -> ToyotaClimate:
    from homeassistant.helpers.entity import EntityDescription

    _entry, coordinator = _coordinator_with(hass, vehicle)
    entity = ToyotaClimate(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=EntityDescription(key="climate", name="Climate"),
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


def test_current_temperature_reflects_cabin_temperature_from_climate_status(
    hass,
) -> None:
    """The car's measured cabin temperature must surface as current_temperature."""
    vehicle = _Vehicle(climate_status=_climate_status(current_temperature=18.5))
    entity = _climate_entity(hass, vehicle)

    entity._load_climate_status_from_coordinator()

    assert entity.current_temperature == 18.5


def test_current_temperature_is_none_when_climate_status_lacks_it(hass) -> None:
    """No reported temperature must stay None, not a crash or a bogus 0."""
    vehicle = _Vehicle(climate_status=_climate_status(current_temperature=None))
    entity = _climate_entity(hass, vehicle)

    entity._load_climate_status_from_coordinator()

    assert entity.current_temperature is None


def test_current_temperature_is_none_when_climate_status_missing(hass) -> None:
    """Vehicles that don't report climate_status at all must not crash."""
    vehicle = _Vehicle(climate_status=None)
    entity = _climate_entity(hass, vehicle)

    entity._load_climate_status_from_coordinator()

    assert entity.current_temperature is None


def test_current_temperature_updates_on_coordinator_refresh(hass) -> None:
    """A later poll reflecting a changed cabin temperature must be picked up."""
    vehicle = _Vehicle(climate_status=_climate_status(current_temperature=18.5))
    entity = _climate_entity(hass, vehicle)
    entity._handle_coordinator_update()
    assert entity.current_temperature == 18.5

    vehicle._climate_status_raw = _climate_status(current_temperature=21.0)
    entity._handle_coordinator_update()
    assert entity.current_temperature == 21.0
