"""Tests for the remote hazard-lights switch and buzzer button (#428)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.endpoints.command import CommandType
from pytoyoda.models.endpoints.common import StatusModel

from custom_components.toyota.button import (
    BUZZER_BUTTON_DESCRIPTION,
    ToyotaBuzzerButton,
)
from custom_components.toyota.button import (
    async_setup_entry as button_async_setup_entry,
)
from custom_components.toyota.const import DOMAIN
from custom_components.toyota.switch import (
    HAZARD_LIGHTS_DESCRIPTION,
    ToyotaHazardLightsSwitch,
    _hazard_capable,
)
from custom_components.toyota.switch import (
    async_setup_entry as switch_async_setup_entry,
)


class _Vehicle:
    """Minimal vehicle shaped like pytoyoda's public command interface."""

    def __init__(
        self,
        *,
        capabilities: dict[str, bool] | None = None,
        command_response: StatusModel | None = None,
        type_: str = "ice",
    ) -> None:
        capabilities = capabilities or {}
        self.vin = "JTDBR32E720000001"
        self.alias = "Test car"
        self.type = type_
        self._vehicle_info = SimpleNamespace(
            brand="T",
            car_model_name="Test",
            extended_capabilities=SimpleNamespace(
                hazard_capable=capabilities.get("extended_hazard", False),
                buzzer_capable=capabilities.get("buzzer", False),
                econnect_vehicle_status_capable=False,
            ),
            remote_service_capabilities=SimpleNamespace(
                hazard_capable=capabilities.get("remote_hazard", False),
            ),
        )
        self.post_command = AsyncMock(return_value=command_response or StatusModel())


def _coordinator(
    hass, vehicles: list[_Vehicle]
) -> tuple[MockConfigEntry, DataUpdateCoordinator]:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [
        {"data": vehicle, "statistics": None, "metric_values": True}
        for vehicle in vehicles
    ]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


# --- switch.py (hazard lights) -----------------------------------------------


@pytest.mark.parametrize("flag", ["extended_hazard", "remote_hazard"])
def test_explicit_capability_flag_makes_vehicle_hazard_capable(flag: str) -> None:
    """Either extended or remote-service hazard flag is sufficient."""
    assert _hazard_capable(_Vehicle(capabilities={flag: True})) is True


def test_missing_capability_flags_are_not_hazard_capable() -> None:
    """A vehicle reporting neither flag must not get the switch."""
    assert _hazard_capable(_Vehicle()) is False


@pytest.mark.asyncio
async def test_switch_setup_only_adds_capable_vehicles(hass) -> None:
    """Only vehicles reporting hazard capability get the switch."""
    entry, _coord = _coordinator(
        hass,
        [_Vehicle(capabilities={"extended_hazard": True}), _Vehicle()],
    )
    entities: list = []

    await switch_async_setup_entry(hass, entry, entities.extend)

    hazard_switches = [e for e in entities if isinstance(e, ToyotaHazardLightsSwitch)]
    assert len(hazard_switches) == 1
    assert hazard_switches[0].entity_description == HAZARD_LIGHTS_DESCRIPTION


def _switch_entity(hass, vehicle: _Vehicle) -> ToyotaHazardLightsSwitch:
    _entry, coordinator = _coordinator(hass, [vehicle])
    entity = ToyotaHazardLightsSwitch(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=HAZARD_LIGHTS_DESCRIPTION,
    )
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


def test_hazard_switch_starts_off_and_is_assumed_state(hass) -> None:
    """With no telemetry available, the switch must start off and assumed."""
    entity = _switch_entity(hass, _Vehicle(capabilities={"extended_hazard": True}))
    assert entity.is_on is False
    assert entity.assumed_state is True


@pytest.mark.asyncio
async def test_hazard_switch_turn_on_sends_hazard_on_command(hass) -> None:
    """Turning on must send CommandType.HAZARD_ON and flip is_on."""
    vehicle = _Vehicle(capabilities={"extended_hazard": True})
    entity = _switch_entity(hass, vehicle)

    await entity.async_turn_on()

    vehicle.post_command.assert_awaited_once_with(CommandType.HAZARD_ON)
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_hazard_switch_turn_off_sends_hazard_off_command(hass) -> None:
    """Turning off must send CommandType.HAZARD_OFF and flip is_on."""
    vehicle = _Vehicle(capabilities={"extended_hazard": True})
    entity = _switch_entity(hass, vehicle)

    await entity.async_turn_on()
    await entity.async_turn_off()

    vehicle.post_command.assert_awaited_with(CommandType.HAZARD_OFF)
    assert entity.is_on is False


@pytest.mark.asyncio
async def test_hazard_switch_rejection_raises_and_does_not_flip_state(hass) -> None:
    """A rejected command must raise and leave the switch showing off."""
    rejected = StatusModel.model_validate({"status": "failed"})
    vehicle = _Vehicle(
        capabilities={"extended_hazard": True}, command_response=rejected
    )
    entity = _switch_entity(hass, vehicle)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    assert entity.is_on is False


@pytest.mark.asyncio
async def test_hazard_switch_transport_error_raises_home_assistant_error(hass) -> None:
    """An exception from post_command must surface as a HomeAssistantError."""
    vehicle = _Vehicle(capabilities={"extended_hazard": True})
    vehicle.post_command = AsyncMock(side_effect=RuntimeError("boom"))
    entity = _switch_entity(hass, vehicle)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    assert entity.is_on is False


# --- button.py (buzzer) -------------------------------------------------------


def test_buzzer_capability_gates_via_extended_capabilities() -> None:
    """The buzzer button must only be relevant for buzzer-capable vehicles."""
    assert (
        _Vehicle(
            capabilities={"buzzer": True}
        )._vehicle_info.extended_capabilities.buzzer_capable
        is True
    )
    assert _Vehicle()._vehicle_info.extended_capabilities.buzzer_capable is False


@pytest.mark.asyncio
async def test_button_setup_only_adds_buzzer_button_for_capable_vehicles(
    hass,
) -> None:
    """Only vehicles reporting buzzer capability get the buzzer button."""
    entry, _coord = _coordinator(
        hass,
        [_Vehicle(capabilities={"buzzer": True}), _Vehicle()],
    )
    entities: list = []

    await button_async_setup_entry(hass, entry, entities.extend)

    buzzer_buttons = [e for e in entities if isinstance(e, ToyotaBuzzerButton)]
    assert len(buzzer_buttons) == 1
    assert buzzer_buttons[0].entity_description == BUZZER_BUTTON_DESCRIPTION


def _buzzer_button(hass, vehicle: _Vehicle) -> ToyotaBuzzerButton:
    _entry, coordinator = _coordinator(hass, [vehicle])
    button = ToyotaBuzzerButton(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=BUZZER_BUTTON_DESCRIPTION,
    )
    button.hass = hass
    return button


@pytest.mark.asyncio
async def test_buzzer_button_press_sends_buzzer_warning_command(hass) -> None:
    """Pressing must send CommandType.BUZZER_WARNING."""
    vehicle = _Vehicle(capabilities={"buzzer": True})
    button = _buzzer_button(hass, vehicle)

    await button.async_press()

    vehicle.post_command.assert_awaited_once_with(CommandType.BUZZER_WARNING)


@pytest.mark.asyncio
async def test_buzzer_button_rejection_raises_home_assistant_error(hass) -> None:
    """A rejected buzzer command must surface as a HomeAssistantError."""
    rejected = StatusModel.model_validate({"code": 403})
    vehicle = _Vehicle(capabilities={"buzzer": True}, command_response=rejected)
    button = _buzzer_button(hass, vehicle)

    with pytest.raises(HomeAssistantError):
        await button.async_press()


@pytest.mark.asyncio
async def test_buzzer_button_transport_error_raises_home_assistant_error(hass) -> None:
    """An exception from post_command must surface as a HomeAssistantError."""
    vehicle = _Vehicle(capabilities={"buzzer": True})
    vehicle.post_command = AsyncMock(side_effect=RuntimeError("boom"))
    button = _buzzer_button(hass, vehicle)

    with pytest.raises(HomeAssistantError):
        await button.async_press()
