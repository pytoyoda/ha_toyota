"""Tests for the seat-heater / steering-wheel-heater controls (issue #54).

Covers the shared ``async_apply_climate_settings`` helper (including the
``_onoff`` bool<->wire-string bug it fixes), the seat-heater select entities,
and the steering-wheel-heater switch entity.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.climate import ClimateSettings
from pytoyoda.models.endpoints.climate import (
    ClimateSettingsResponseModel,
    RemoteClimateControlResponseModel,
)

from custom_components.toyota.climate import (
    _onoff,
    async_apply_climate_settings,
)
from custom_components.toyota.const import DOMAIN
from custom_components.toyota.select import (
    SEAT_HEATER_OPTIONS,
    ToyotaSeatHeaterSelect,
)
from custom_components.toyota.select import (
    async_setup_entry as select_async_setup_entry,
)
from custom_components.toyota.switch import (
    STEERING_HEATER_DESCRIPTION,
    ToyotaSteeringHeaterSwitch,
)
from custom_components.toyota.switch import (
    async_setup_entry as switch_async_setup_entry,
)


def _climate_settings(
    *,
    front_defroster: str | None = "off",
    rear_defogger: str | None = "off",
    steering_heater: str | None = "off",
    driver_seat: str | None = "off",
    passenger_seat: str | None = "off",
    rear_driver_seat: str | None = None,
    rear_passenger_seat: str | None = None,
    temperature: float | None = 22,
) -> ClimateSettingsResponseModel:
    """Build a real climate-settings response, exercising pytoyoda's own models."""
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
                    "driverSeat": driver_seat,
                    "passengerSeat": passenger_seat,
                    "rearDriverSeat": rear_driver_seat,
                    "rearPassengerSeat": rear_passenger_seat,
                },
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
        set_climate_response: RemoteClimateControlResponseModel | None = None,
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
        self.set_climate = AsyncMock(
            return_value=set_climate_response or _ok_response()
        )

    @property
    def climate_settings(self) -> ClimateSettings | None:
        raw = self._endpoint_data.get("climate_settings")
        return ClimateSettings(raw) if raw is not None else None


# --- _onoff -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"), [(True, "on"), (False, "off"), (None, None)]
)
def test_onoff_converts_tristate_bool_to_wire_string(
    value: bool | None, expected: str | None
) -> None:
    """The read side hands back bool | None; the write side needs the string."""
    assert _onoff(value=value) == expected


# --- async_apply_climate_settings --------------------------------------------


@pytest.mark.asyncio
async def test_apply_settings_preserves_steering_heater_when_only_seat_changes() -> (
    None
):
    """Regression test: echoing a bool read must not drop the field to None.

    Before the fix, ``HeatingOptions.steering_heater`` (bool) was passed
    straight into ``HeatingOptionsModel`` (which expects "on"/"off"),
    silently coercing to None and vanishing from the outgoing request
    (``model_dump(exclude_none=True)``).
    """
    vehicle = _Vehicle(
        climate_settings=_climate_settings(
            steering_heater="on", driver_seat="off"
        )
    )

    await async_apply_climate_settings(
        vehicle, seat_overrides={"driver_seat": "medium"}
    )

    request = vehicle.set_climate.call_args.args[0]
    assert request.heating_options.steering_heater == "on"
    assert request.seat_options.driver_seat == "medium"
    # Unrelated seats are echoed unchanged.
    assert request.seat_options.passenger_seat == "off"
    assert request.command == "start"
    assert request.save_settings is True


@pytest.mark.asyncio
async def test_apply_settings_overrides_steering_heater_explicitly() -> None:
    """An explicit steering_heater kwarg wins over the echoed read value."""
    vehicle = _Vehicle(climate_settings=_climate_settings(steering_heater="off"))

    await async_apply_climate_settings(vehicle, steering_heater="on")

    request = vehicle.set_climate.call_args.args[0]
    assert request.heating_options.steering_heater == "on"


@pytest.mark.asyncio
async def test_apply_settings_raises_on_rejection() -> None:
    """A rejected command must surface as a HomeAssistantError, not silently fail."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(),
        set_climate_response=_rejected_response(),
    )

    with pytest.raises(HomeAssistantError):
        await async_apply_climate_settings(vehicle, steering_heater="on")


# --- select.py (seat-heater level) --------------------------------------------


def _coordinator_with(
    hass, vehicle: _Vehicle
) -> tuple[MockConfigEntry, DataUpdateCoordinator]:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(
        hass, Mock(), config_entry=entry, name="test"
    )
    coordinator.data = [
        {"data": vehicle, "statistics": None, "metric_values": True}
    ]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


@pytest.mark.asyncio
async def test_select_setup_only_adds_seats_present_on_the_vehicle(hass) -> None:
    """A vehicle reporting only two seat heaters must not get four entities."""
    vehicle = _Vehicle(
        capabilities={"features": True},
        climate_settings=_climate_settings(
            driver_seat="off",
            passenger_seat="low",
            rear_driver_seat=None,
            rear_passenger_seat=None,
        ),
    )
    entry, _coord = _coordinator_with(hass, vehicle)
    entities: list[ToyotaSeatHeaterSelect] = []

    await select_async_setup_entry(
        hass,
        entry,
        entities.extend,
    )

    assert {e._seat_field for e in entities} == {"driver_seat", "passenger_seat"}


@pytest.mark.asyncio
async def test_select_setup_skips_vehicle_without_climate_capability(hass) -> None:
    """An incapable vehicle must not get seat-heater controls at all."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(driver_seat="off"),
    )
    entry, _coord = _coordinator_with(hass, vehicle)
    entities: list[ToyotaSeatHeaterSelect] = []

    await select_async_setup_entry(hass, entry, entities.extend)

    assert entities == []


def _select_entity(hass, vehicle: _Vehicle, seat_field: str) -> ToyotaSeatHeaterSelect:
    from homeassistant.components.select import SelectEntityDescription

    _entry, coordinator = _coordinator_with(hass, vehicle)
    entity = ToyotaSeatHeaterSelect(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=SelectEntityDescription(key=seat_field, name=seat_field),
        seat_field=seat_field,
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


def test_select_current_option_reflects_the_read_value(hass) -> None:
    """With no pending write, the select must reflect the live read."""
    vehicle = _Vehicle(climate_settings=_climate_settings(driver_seat="medium"))
    entity = _select_entity(hass, vehicle, "driver_seat")
    assert entity.current_option == "medium"


def test_select_options_are_the_four_backend_levels(hass) -> None:
    """The select must expose exactly the backend's four heater levels."""
    vehicle = _Vehicle(climate_settings=_climate_settings())
    entity = _select_entity(hass, vehicle, "driver_seat")
    assert entity.options == list(SEAT_HEATER_OPTIONS)


@pytest.mark.asyncio
async def test_select_option_is_optimistic_then_confirmed(hass) -> None:
    """Picking a level shows immediately and survives a stale coordinator poll."""
    vehicle = _Vehicle(climate_settings=_climate_settings(driver_seat="off"))
    entity = _select_entity(hass, vehicle, "driver_seat")

    await entity.async_select_option("high")
    assert entity.current_option == "high"

    # A coordinator update that hasn't caught up yet must not revert it.
    entity._handle_coordinator_update()
    assert entity.current_option == "high"

    # Once the backend read catches up, the pending override clears.
    vehicle._endpoint_data["climate_settings"] = _climate_settings(driver_seat="high")
    entity._handle_coordinator_update()
    assert entity.current_option == "high"
    assert entity._pending_option is None


@pytest.mark.asyncio
async def test_select_option_rejection_reverts_and_raises(hass) -> None:
    """A rejected command must not leave the tile showing the failed value."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(driver_seat="off"),
        set_climate_response=_rejected_response(),
    )
    entity = _select_entity(hass, vehicle, "driver_seat")

    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("high")

    assert entity.current_option == "off"


# --- switch.py (steering-wheel heater) ---------------------------------------


@pytest.mark.asyncio
async def test_switch_setup_adds_entity_only_when_steering_heater_present(
    hass,
) -> None:
    """A vehicle without steering-heater data must not get the switch."""
    vehicle = _Vehicle(
        capabilities={"features": True},
        climate_settings=_climate_settings(steering_heater=None),
    )
    entry, _coord = _coordinator_with(hass, vehicle)
    entities: list[ToyotaSteeringHeaterSwitch] = []

    await switch_async_setup_entry(hass, entry, entities.extend)

    assert entities == []


@pytest.mark.asyncio
async def test_switch_setup_adds_entity_when_capable_and_present(hass) -> None:
    """A capable vehicle reporting a steering-heater state gets the switch."""
    vehicle = _Vehicle(
        capabilities={"features": True},
        climate_settings=_climate_settings(steering_heater="off"),
    )
    entry, _coord = _coordinator_with(hass, vehicle)
    entities: list[ToyotaSteeringHeaterSwitch] = []

    await switch_async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == 1
    assert entities[0].entity_description == STEERING_HEATER_DESCRIPTION


def _switch_entity(hass, vehicle: _Vehicle) -> ToyotaSteeringHeaterSwitch:
    _entry, coordinator = _coordinator_with(hass, vehicle)
    entity = ToyotaSteeringHeaterSwitch(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=STEERING_HEATER_DESCRIPTION,
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


def test_switch_is_on_reflects_the_read_value(hass) -> None:
    """With no pending write, is_on must reflect the live read."""
    vehicle = _Vehicle(climate_settings=_climate_settings(steering_heater="on"))
    entity = _switch_entity(hass, vehicle)
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_switch_turn_on_is_optimistic_then_confirmed(hass) -> None:
    """Turning it on shows immediately and survives a stale coordinator poll."""
    vehicle = _Vehicle(climate_settings=_climate_settings(steering_heater="off"))
    entity = _switch_entity(hass, vehicle)

    await entity.async_turn_on()
    assert entity.is_on is True

    entity._handle_coordinator_update()
    assert entity.is_on is True

    vehicle._endpoint_data["climate_settings"] = _climate_settings(
        steering_heater="on"
    )
    entity._handle_coordinator_update()
    assert entity.is_on is True
    assert entity._pending_state is None


@pytest.mark.asyncio
async def test_switch_turn_on_rejection_reverts_and_raises(hass) -> None:
    """A rejected command must not leave the switch showing the failed state."""
    vehicle = _Vehicle(
        climate_settings=_climate_settings(steering_heater="off"),
        set_climate_response=_rejected_response(),
    )
    entity = _switch_entity(hass, vehicle)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    assert entity.is_on is False


@pytest.mark.asyncio
async def test_switch_sends_the_correct_wire_value(hass) -> None:
    """turn_on/turn_off must send "on"/"off" strings, not Python bools."""
    vehicle = _Vehicle(climate_settings=_climate_settings(steering_heater="off"))
    entity = _switch_entity(hass, vehicle)

    await entity.async_turn_on()
    request = vehicle.set_climate.call_args.args[0]
    assert request.heating_options.steering_heater == "on"

    await entity.async_turn_off()
    request = vehicle.set_climate.call_args.args[0]
    assert request.heating_options.steering_heater == "off"
