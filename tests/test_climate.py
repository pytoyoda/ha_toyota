"""Tests for the Toyota climate control entity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.climate import ClimateSettings, ClimateStatus
from pytoyoda.models.endpoints.climate import (
    ClimateSettingsResponseModel,
    ClimateStatusResponseModel,
    RemoteClimateControlResponseModel,
)

from custom_components.toyota.climate import (
    ToyotaClimate,
    _vehicle_has_climate_capability,
    async_setup_entry,
)
from custom_components.toyota.const import DOMAIN

CLIMATE_DESCRIPTION = EntityDescription(key="climate", name="Climate")


def _climate_settings(
    *,
    front_defroster: str | None = "off",
    rear_defogger: str | None = "off",
    steering_heater: str | None = "off",
    temperature: float | None = 22,
) -> ClimateSettingsResponseModel:
    return ClimateSettingsResponseModel.model_validate(
        {
            "payload": {
                "duration": 15,
                "temperature": (
                    {"unit": "C", "value": temperature}
                    if temperature is not None
                    else None
                ),
                "heatingOptions": {
                    "frontDefroster": front_defroster,
                    "rearDefogger": rear_defogger,
                    "steeringHeater": steering_heater,
                },
                "seatOptions": {
                    "driverSeat": "off",
                    "passengerSeat": "off",
                },
            }
        }
    )


def _climate_status(
    *,
    status: str | None = "stopped",
    current_temperature: float | None = None,
) -> ClimateStatusResponseModel:
    return ClimateStatusResponseModel.model_validate(
        {
            "payload": {
                "status": status,
                "currentTemperature": (
                    {"unit": "C", "value": current_temperature}
                    if current_temperature is not None
                    else None
                ),
            }
        }
    )


def _ok_response() -> RemoteClimateControlResponseModel:
    return RemoteClimateControlResponseModel.model_validate(
        {"payload": {"appRequestNo": "1", "returnCode": "000000"}}
    )


def _rejected_response() -> RemoteClimateControlResponseModel:
    return RemoteClimateControlResponseModel.model_validate(
        {"payload": {"appRequestNo": "1", "returnCode": "118003"}}
    )


class _Vehicle:
    """Minimal vehicle shaped like pytoyoda's public climate interfaces."""

    def __init__(
        self,
        *,
        capabilities: dict[str, bool] | None = None,
        climate_settings: ClimateSettingsResponseModel | None = None,
        climate_status: ClimateStatusResponseModel | None = None,
        set_climate_response: RemoteClimateControlResponseModel | None = None,
        refresh_climate_status_result: bool = True,
    ) -> None:
        capabilities = capabilities or {}
        self.vin = "JTDBR32E720000001"
        self.alias = "Test car"
        self._vehicle_info = SimpleNamespace(
            brand="T",
            car_model_name="Test",
            features=SimpleNamespace(
                climate_start_engine=capabilities.get("features", False)
            ),
            extended_capabilities=SimpleNamespace(
                climate_capable=capabilities.get("climate_capable", False),
                econnect_climate_capable=capabilities.get(
                    "econnect_climate_capable", False
                ),
                remote_engine_start_stop=capabilities.get(
                    "remote_engine_start_stop", False
                ),
            ),
        )
        self._endpoint_data: dict[str, object] = {}
        if climate_settings is not None:
            self._endpoint_data["climate_settings"] = climate_settings
        if climate_status is not None:
            self._endpoint_data["climate_status"] = climate_status
        self.set_climate = AsyncMock(
            return_value=set_climate_response or _ok_response()
        )
        self.refresh_climate_status = AsyncMock(
            return_value=refresh_climate_status_result
        )
        self.update = AsyncMock()

    @property
    def climate_settings(self) -> ClimateSettings | None:
        raw = self._endpoint_data.get("climate_settings")
        return ClimateSettings(raw) if raw is not None else None

    @property
    def climate_status(self) -> ClimateStatus | None:
        raw = self._endpoint_data.get("climate_status")
        return ClimateStatus(raw) if raw is not None else None


# --- capability gating --------------------------------------------------


def test_legacy_feature_flag_grants_capability() -> None:
    """The old ICE/hybrid feature flag alone must enable climate."""
    vehicle = _Vehicle(capabilities={"features": True})
    assert _vehicle_has_climate_capability(vehicle) is True


@pytest.mark.parametrize(
    "cap",
    ["climate_capable", "econnect_climate_capable", "remote_engine_start_stop"],
)
def test_extended_capability_flags_grant_capability(cap: str) -> None:
    """Each PHEV/EV extended-capability flag alone must enable climate."""
    vehicle = _Vehicle(capabilities={cap: True})
    assert _vehicle_has_climate_capability(vehicle) is True


def test_no_capability_flags_means_no_climate() -> None:
    """A vehicle with none of the known flags must not get a climate entity."""
    assert _vehicle_has_climate_capability(_Vehicle()) is False


def test_capability_check_is_defensive_against_missing_attrs() -> None:
    """A vehicle info object missing the expected attributes must not crash setup."""
    vehicle = SimpleNamespace(_vehicle_info=object())
    assert _vehicle_has_climate_capability(vehicle) is False


@pytest.mark.asyncio
async def test_setup_adds_only_capable_vehicles(hass) -> None:
    """Setup must add exactly one entity for the one capable vehicle."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [
        {
            "data": _Vehicle(capabilities={"features": True}),
            "statistics": None,
            "metric_values": True,
        },
        {"data": _Vehicle(), "statistics": None, "metric_values": True},
    ]
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    entities: list[ToyotaClimate] = []

    await async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == 1


# --- entity construction / state loading ---------------------------------


def _entity(hass, vehicle: _Vehicle) -> ToyotaClimate:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [
        {"data": vehicle, "statistics": None, "metric_values": True}
    ]
    entity = ToyotaClimate(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=CLIMATE_DESCRIPTION,
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


def test_entity_defaults_when_no_climate_settings(hass) -> None:
    """A cold cache (no climate_settings yet) must not crash and stays off."""
    entity = _entity(hass, _Vehicle())
    assert entity.hvac_mode == HVACMode.OFF
    assert entity.target_temperature == 21
    assert entity.preset_mode == "none"


def test_entity_loads_temperature_and_defrost_from_settings(hass) -> None:
    """Climate settings must populate temperature and defrost/defogger state."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(
            front_defroster="on", rear_defogger="on", temperature=24
        )
    )
    entity = _entity(hass, vehicle)
    assert entity.target_temperature == 24
    assert entity.front_defrost is True
    assert entity.rear_defrost is True
    assert entity.preset_mode == "both_defrost"


@pytest.mark.parametrize(
    ("front", "rear", "preset"),
    [
        (True, False, "front_defrost"),
        (False, True, "rear_defrost"),
        (False, False, "none"),
    ],
)
def test_preset_mode_reflects_defrost_combination(
    hass, front: bool, rear: bool, preset: str
) -> None:
    """Each front/rear defrost combination must map to the right preset name."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(
            front_defroster="on" if front else "off",
            rear_defogger="on" if rear else "off",
        )
    )
    entity = _entity(hass, vehicle)
    assert entity.preset_mode == preset


def test_climate_status_updates_hvac_mode_and_current_temperature(hass) -> None:
    """A live climate_status read must be reflected on the entity."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(),
        climate_status=_climate_status(status="running", current_temperature=19),
    )
    entity = _entity(hass, vehicle)
    entity._load_climate_status_from_coordinator()
    assert entity.hvac_mode == HVACMode.HEAT_COOL
    assert entity.current_temperature == 19


def test_missing_climate_status_leaves_state_unchanged(hass) -> None:
    """No climate_status yet must not crash and must not flip hvac_mode."""
    entity = _entity(hass, _Vehicle(climate_settings=_climate_settings()))
    entity._load_climate_status_from_coordinator()
    assert entity.hvac_mode == HVACMode.OFF


def test_extra_state_attributes_expose_heater_levels(hass) -> None:
    """Seat/steering heater state must surface as extra attributes."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(steering_heater="on")
    )
    entity = _entity(hass, vehicle)
    attrs = entity.extra_state_attributes
    assert attrs["steering_heater"] is True
    assert attrs["seat_heater_driver"] == "off"
    assert attrs["duration_minutes"] == 15


def test_extra_state_attributes_none_without_settings(hass) -> None:
    """No climate_settings read yet must return None, not raise."""
    entity = _entity(hass, _Vehicle())
    assert entity.extra_state_attributes is None


# --- preset mode / temperature setters -----------------------------------


@pytest.mark.asyncio
async def test_set_preset_mode_updates_defrost_flags_and_marks_dirty(hass) -> None:
    """Choosing a preset must set the right defrost flags and mark dirty."""
    entity = _entity(hass, _Vehicle(climate_settings=_climate_settings()))

    await entity.async_set_preset_mode("both_defrost")
    assert entity.front_defrost is True
    assert entity.rear_defrost is True
    assert entity._settings_dirty is True


@pytest.mark.asyncio
async def test_set_temperature_updates_target_and_marks_dirty(hass) -> None:
    """Setting a temperature must update the local target and mark dirty."""
    entity = _entity(hass, _Vehicle(climate_settings=_climate_settings()))

    await entity.async_set_temperature(temperature=25)
    assert entity.target_temperature == 25
    assert entity._settings_dirty is True


@pytest.mark.asyncio
async def test_set_temperature_without_value_is_noop(hass) -> None:
    """A set_temperature call missing the temperature kwarg must not crash."""
    entity = _entity(hass, _Vehicle(climate_settings=_climate_settings()))
    before = entity.target_temperature

    await entity.async_set_temperature()

    assert entity.target_temperature == before
    assert entity._settings_dirty is False


@pytest.mark.asyncio
async def test_dirty_settings_are_not_overwritten_by_coordinator_update(hass) -> None:
    """A pending user edit must survive a coordinator refresh before the start."""
    vehicle = _Vehicle(climate_settings=_climate_settings(temperature=20))
    entity = _entity(hass, vehicle)

    await entity.async_set_temperature(temperature=27)
    entity._handle_coordinator_update()

    assert entity.target_temperature == 27


# --- turn on / off --------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_on_climate_sends_start_and_confirms(hass) -> None:
    """A successful start must land as HEAT_COOL and clear the dirty flag."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(),
        climate_status=_climate_status(status="running"),
    )
    entity = _entity(hass, vehicle)

    await entity.async_turn_on()

    assert entity.hvac_mode == HVACMode.HEAT_COOL
    vehicle.set_climate.assert_awaited_once()
    request = vehicle.set_climate.call_args.args[0]
    assert request.command == "start"


@pytest.mark.asyncio
async def test_turn_on_climate_rejection_rolls_back(hass) -> None:
    """A rejected start must roll back the optimistic HEAT_COOL state."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(),
        set_climate_response=_rejected_response(),
    )
    entity = _entity(hass, vehicle)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    assert entity.hvac_mode == HVACMode.OFF


@pytest.mark.asyncio
async def test_turn_off_climate_sends_stop(hass) -> None:
    """Turning off must send a stop command and land as OFF."""
    vehicle = _Vehicle(climate_settings=_climate_settings())
    entity = _entity(hass, vehicle)
    entity._attr_hvac_mode = HVACMode.HEAT_COOL

    await entity.async_turn_off()

    assert entity.hvac_mode == HVACMode.OFF
    request = vehicle.set_climate.call_args.args[0]
    assert request.command == "stop"


@pytest.mark.asyncio
async def test_turn_off_climate_error_rolls_back_to_on(hass) -> None:
    """A transport failure on stop must revert to HEAT_COOL, not falsely show off."""
    vehicle = _Vehicle(climate_settings=_climate_settings())
    vehicle.set_climate.side_effect = RuntimeError("boom")
    entity = _entity(hass, vehicle)
    entity._attr_hvac_mode = HVACMode.HEAT_COOL

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()

    assert entity.hvac_mode == HVACMode.HEAT_COOL


@pytest.mark.asyncio
async def test_set_hvac_mode_dispatches_to_turn_on_and_off(hass) -> None:
    """set_hvac_mode must route to the matching turn_on/turn_off helpers."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(),
        climate_status=_climate_status(status="running"),
    )
    entity = _entity(hass, vehicle)

    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    assert entity.hvac_mode == HVACMode.HEAT_COOL

    await entity.async_set_hvac_mode(HVACMode.OFF)
    assert entity.hvac_mode == HVACMode.OFF


@pytest.mark.asyncio
async def test_async_update_polls_only_when_climate_is_on(hass) -> None:
    """Polling must be skipped entirely while climate is off (saves airtime)."""
    vehicle = _Vehicle(climate_settings=_climate_settings())
    entity = _entity(hass, vehicle)

    await entity.async_update()

    vehicle.refresh_climate_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_update_polls_status_when_climate_is_on(hass) -> None:
    """When climate is on, async_update must refresh + reload climate_status."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(),
        climate_status=_climate_status(status="running", current_temperature=21),
    )
    entity = _entity(hass, vehicle)
    entity._attr_hvac_mode = HVACMode.HEAT_COOL

    await entity.async_update()

    vehicle.refresh_climate_status.assert_awaited_once()
    vehicle.update.assert_awaited_once_with(only=["climate_status"])
    assert entity.current_temperature == 21
