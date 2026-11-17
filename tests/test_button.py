"""Tests for the Toyota refresh-status/trips/electric button entities."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.endpoints.electric import (
    ChargeCommandType,
    ElectricCommandResponseModel,
    ElectricResponseModel,
    NextChargeSettings,
)
from pytoyoda.models.dashboard import Dashboard

from custom_components.toyota.button import (
    CHARGE_NOW_BUTTON_DESCRIPTION,
    REFRESH_BUTTON_DESCRIPTION,
    REFRESH_ELECTRIC_REALTIME_STATUS_BUTTON_DESCRIPTION,
    REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION,
    ToyotaChargeNowButton,
    ToyotaRefreshElectricRealtimeStatusButton,
    ToyotaRefreshRecentTripsButton,
    ToyotaRefreshStatusButton,
    async_setup_entry,
)
from custom_components.toyota.const import CONF_MAX_RECENT_TRIPS, DOMAIN


@pytest.fixture(autouse=True)
def charge_refresh_sleep():
    """Skip post-command delays without changing Home Assistant's event loop."""
    sleep = AsyncMock()
    with patch(
        "custom_components.toyota.button.asyncio",
        SimpleNamespace(sleep=sleep, timeout=asyncio.timeout),
    ):
        yield sleep


class _Vehicle:
    """Minimal vehicle matching the capability checks button.py relies on."""

    def __init__(
        self,
        *,
        type_: str = "ice",
        econnect: bool = False,
        next_charge: bool | None = None,
        charge_management: bool | None = None,
        can_set_next_charging_event: bool = False,
    ) -> None:
        self.vin = "JTDBR32E720000001"
        self.alias = "Test car"
        self.type = type_
        self.dashboard = SimpleNamespace(charging_status="plugged", battery_level=80)
        self.refresh_electric_realtime_status = AsyncMock(
            return_value=SimpleNamespace(code=200)
        )
        self.electric_status = SimpleNamespace(
            can_set_next_charging_event=can_set_next_charging_event
        )
        self._vehicle_info = SimpleNamespace(
            brand="T",
            car_model_name="Test",
            extended_capabilities=SimpleNamespace(
                econnect_vehicle_status_capable=econnect,
                next_charge=next_charge,
                charge_management=charge_management,
            ),
        )


def _coordinator(hass, vehicles: list[_Vehicle]) -> tuple[MockConfigEntry, DataUpdateCoordinator]:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id")
    entry.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(hass, Mock(), config_entry=entry, name="test")
    coordinator.data = [
        {"data": vehicle, "statistics": None, "metric_values": True}
        for vehicle in vehicles
    ]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


@pytest.mark.asyncio
async def test_setup_adds_status_and_trips_buttons_for_every_vehicle(hass) -> None:
    """Every vehicle gets a status button and a recent-trips button."""
    entry, _coord = _coordinator(hass, [_Vehicle(), _Vehicle()])
    entities: list = []

    await async_setup_entry(hass, entry, entities.extend)

    status_buttons = [e for e in entities if isinstance(e, ToyotaRefreshStatusButton)]
    trip_buttons = [
        e for e in entities if isinstance(e, ToyotaRefreshRecentTripsButton)
    ]
    assert len(status_buttons) == 2
    assert len(trip_buttons) == 2


@pytest.mark.asyncio
async def test_setup_adds_electric_button_only_when_capable(hass) -> None:
    """The electric-realtime-status button is gated on capability or EV/PHEV type."""
    entry, _coord = _coordinator(
        hass,
        [
            _Vehicle(econnect=True),
            _Vehicle(type_="electric"),
            _Vehicle(type_="plug_in_hybrid"),
            _Vehicle(),
        ],
    )
    entities: list = []

    await async_setup_entry(hass, entry, entities.extend)

    electric_buttons = [
        e
        for e in entities
        if isinstance(e, ToyotaRefreshElectricRealtimeStatusButton)
    ]
    assert len(electric_buttons) == 3


def _button(hass, cls, vehicle: _Vehicle, description) -> object:
    _entry, coordinator = _coordinator(hass, [vehicle])
    button = cls(
        coordinator=coordinator,
        entry_id="entry-id",
        vehicle_index=0,
        description=description,
    )
    button.hass = hass
    return button


@pytest.mark.asyncio
async def test_status_button_without_device_is_a_noop(hass) -> None:
    """Pressing before the device is registered must not raise."""
    button = _button(hass, ToyotaRefreshStatusButton, _Vehicle(), REFRESH_BUTTON_DESCRIPTION)
    await button.async_press()  # must not raise


@pytest.mark.asyncio
async def test_status_button_calls_refresh_service_with_device_id(hass) -> None:
    """Pressing calls toyota.refresh_vehicle_status with the right device id."""
    vehicle = _Vehicle()
    button = _button(hass, ToyotaRefreshStatusButton, vehicle, REFRESH_BUTTON_DESCRIPTION)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id", identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_vehicle_status", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id]}]


@pytest.mark.asyncio
async def test_recent_trips_button_uses_configured_max_trips(hass) -> None:
    """The limit sent must be the configured max_recent_trips option."""
    vehicle = _Vehicle()
    entry, coordinator = _coordinator(hass, [vehicle])
    hass.config_entries._entries[entry.entry_id] = entry
    hass.config_entries.async_update_entry(entry, options={CONF_MAX_RECENT_TRIPS: 12})
    button = ToyotaRefreshRecentTripsButton(
        coordinator=coordinator,
        entry_id=entry.entry_id,
        vehicle_index=0,
        description=REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION,
    )
    button.hass = hass
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_recent_trips", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id], "limit": 12}]


@pytest.mark.asyncio
async def test_recent_trips_button_falls_back_when_auto_fetch_disabled(hass) -> None:
    """max_recent_trips=0 (auto-fetch off) must fall back to the manual default."""
    vehicle = _Vehicle()
    entry, coordinator = _coordinator(hass, [vehicle])
    hass.config_entries.async_update_entry(entry, options={CONF_MAX_RECENT_TRIPS: 0})
    button = ToyotaRefreshRecentTripsButton(
        coordinator=coordinator,
        entry_id=entry.entry_id,
        vehicle_index=0,
        description=REFRESH_RECENT_TRIPS_BUTTON_DESCRIPTION,
    )
    button.hass = hass
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_recent_trips", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id], "limit": 5}]


@pytest.mark.asyncio
async def test_electric_button_calls_electric_refresh_service(hass) -> None:
    """Pressing the electric-status button calls the matching service."""
    vehicle = _Vehicle(econnect=True)
    button = _button(
        hass,
        ToyotaRefreshElectricRealtimeStatusButton,
        vehicle,
        REFRESH_ELECTRIC_REALTIME_STATUS_BUTTON_DESCRIPTION,
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id", identifiers={(DOMAIN, vehicle.vin)}
    )
    calls: list[dict] = []

    async def handle(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "refresh_electric_realtime_status", handle)

    await button.async_press()
    await hass.async_block_till_done()

    assert calls == [{"device_id": [device.id]}]


@pytest.mark.asyncio
async def test_setup_adds_charge_now_button_only_when_capable(hass) -> None:
    """Explicit next-charge or charge-management support creates a button."""
    missing_capability = _Vehicle(type_="electric")
    del missing_capability._vehicle_info.extended_capabilities.next_charge
    entry, _coord = _coordinator(
        hass,
        [
            _Vehicle(type_="electric", next_charge=True),
            _Vehicle(type_="plug_in_hybrid", next_charge=True),
            _Vehicle(type_="ice", next_charge=True),
            _Vehicle(type_="electric", charge_management=True),
            _Vehicle(type_="electric", next_charge=False),
            _Vehicle(type_="plug_in_hybrid"),
            _Vehicle(type_="ice"),
            missing_capability,
        ],
    )
    entities: list = []

    await async_setup_entry(hass, entry, entities.extend)

    charge_buttons = [e for e in entities if isinstance(e, ToyotaChargeNowButton)]
    assert len(charge_buttons) == 4
    assert all(
        (
            button.vehicle._vehicle_info.extended_capabilities.next_charge is True
            or button.vehicle._vehicle_info.extended_capabilities.charge_management
            is True
        )
        for button in charge_buttons
    )


@pytest.mark.asyncio
async def test_charge_now_button_sends_charge_now_command(hass) -> None:
    """The button sends pytoyoda's charge-now next-charge command."""
    vehicle = _Vehicle(type_="electric", can_set_next_charging_event=True)
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id", identifiers={(DOMAIN, vehicle.vin)}
    )
    refresh = AsyncMock()
    hass.services.async_register(DOMAIN, "refresh_electric_realtime_status", refresh)
    vehicle.send_next_charging_command = AsyncMock(
        return_value=ElectricCommandResponseModel.model_validate(
            {"payload": {"returnCode": "000000"}}
        )
    )

    await button.async_press()
    await button._charge_refresh_task
    await hass.async_block_till_done()

    vehicle.send_next_charging_command.assert_awaited_once()
    command = vehicle.send_next_charging_command.await_args.args[0]
    assert isinstance(command, NextChargeSettings)
    assert command.command == ChargeCommandType.CHARGE_NOW
    assert command.model_dump(exclude_none=True, by_alias=True) == {
        "command": "CHARGE_NOW"
    }
    refresh.assert_awaited_once()
    assert refresh.await_args.args[0].data == {"device_id": [device.id]}
    vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_now_delays_refresh(hass, charge_refresh_sleep) -> None:
    """Schedule just one refresh after allowing the command to be processed."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock(
        return_value=ElectricCommandResponseModel.model_validate(
            {"payload": {"returnCode": "000000"}}
        )
    )
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    await button.async_press()
    await button._charge_refresh_task
    charge_refresh_sleep.assert_awaited_once_with(30)
    vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_now_refresh_publishes_unavailable_when_charging(hass) -> None:
    """Real coordinator refreshes replace snapshots and publish button availability."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock(
        return_value=ElectricCommandResponseModel.model_validate(
            {"payload": {"returnCode": "000000"}}
        )
    )
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    button.coordinator.data[0]["is_cached"] = True
    button.entity_id = "button.test_charge_now"
    snapshots = []
    for status in ("plugged", "charging"):
        updated_vehicle = _Vehicle(type_="electric")
        updated_vehicle.dashboard = Dashboard(
            None,
            ElectricResponseModel.model_validate(
                {"payload": {"chargingStatus": status, "batteryLevel": 80}}
            ),
            None,
            True,
        )
        snapshots.append([{**button.coordinator.data[0], "data": updated_vehicle}])
    button.coordinator.update_method = AsyncMock(side_effect=snapshots)
    dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id", identifiers={(DOMAIN, vehicle.vin)}
    )

    async def refresh_service(call):
        await button.coordinator.async_refresh()
        await button.coordinator.async_refresh()

    hass.services.async_register(
        DOMAIN, "refresh_electric_realtime_status", refresh_service
    )
    component = EntityComponent(logging.getLogger(__name__), "button", hass)
    await component.async_add_entities([button])
    try:
        assert hass.states.get(button.entity_id).state != "unavailable"
        await button.async_press()
        await button._charge_refresh_task
        await hass.async_block_till_done()
        assert button.coordinator.update_method.await_count == 2
        assert button.vehicle.dashboard.charging_status == "charging"
        assert not button.available
        assert hass.states.get(button.entity_id).state == "unavailable"
        with pytest.raises(HomeAssistantError, match="already charging"):
            await button.async_press()
        # Manual electric refreshes and normal polls use the same notification.
        button.coordinator.async_set_updated_data(snapshots[0])
        assert hass.states.get(button.entity_id).state != "unavailable"
        button.coordinator.async_set_updated_data(snapshots[1])
        assert hass.states.get(button.entity_id).state == "unavailable"
    finally:
        await component.async_remove_entity(button.entity_id)


@pytest.mark.asyncio
async def test_charge_now_uses_latest_snapshot_before_listener_runs(hass) -> None:
    """Availability and press guards do not depend on the cached entity vehicle."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock()
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    charging_vehicle = _Vehicle(type_="electric")
    charging_vehicle.dashboard.charging_status = "charging"
    button.coordinator.data = [
        {**button.coordinator.data[0], "data": charging_vehicle, "is_cached": True}
    ]

    assert button.vehicle is vehicle
    assert not button.available
    with pytest.raises(HomeAssistantError, match="already charging"):
        await button.async_press()
    vehicle.send_next_charging_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_now_skips_refresh_when_already_charging(hass) -> None:
    """A poll confirming charging before the delay ends avoids another wake."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock(
        return_value=ElectricCommandResponseModel.model_validate(
            {"payload": {"returnCode": "000000"}}
        )
    )
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    await button.async_press()
    vehicle.dashboard.charging_status = "charging"
    await button._charge_refresh_task

    vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_now_repeated_presses_share_pending_refresh(hass) -> None:
    """Repeated presses do not schedule overlapping wake and refresh tasks."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock(
        return_value=ElectricCommandResponseModel.model_validate(
            {"payload": {"returnCode": "000000"}}
        )
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def delay(seconds):
        started.set()
        await release.wait()
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    dr.async_get(hass).async_get_or_create(
        config_entry_id="entry-id", identifiers={(DOMAIN, vehicle.vin)}
    )
    refresh = AsyncMock()
    hass.services.async_register(DOMAIN, "refresh_electric_realtime_status", refresh)
    with patch("custom_components.toyota.button.asyncio.sleep", side_effect=delay):
        await button.async_press()
        await started.wait()
        task = button._charge_refresh_task
        await button.async_press()
        assert button._charge_refresh_task is task
        release.set()
        await task
    await hass.async_block_till_done()

    assert vehicle.send_next_charging_command.await_count == 2
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_charge_now_cancels_pending_refresh_on_removal(hass) -> None:
    """Removing the entity cancels its pending post-command work."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock(
        return_value=ElectricCommandResponseModel.model_validate(
            {"payload": {"returnCode": "000000"}}
        )
    )
    started = asyncio.Event()

    async def delay(seconds):
        started.set()
        await asyncio.Event().wait()

    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    button.coordinator.async_refresh = AsyncMock()

    with patch("custom_components.toyota.button.asyncio.sleep", side_effect=delay):
        await button.async_press()
        await started.wait()
        task = button._charge_refresh_task
        button._cancel_charge_refresh()
        with pytest.raises(asyncio.CancelledError):
            await task
    button.coordinator.async_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            {"payload": {"returnCode": "123456"}},
            "return code 123456",
        ),
        (
            {"code": 429},
            "HTTP 429",
        ),
    ],
)
async def test_charge_now_button_reports_rejection(hass, response, error) -> None:
    """Explicit Toyota command failures are surfaced to Home Assistant."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock(
        return_value=ElectricCommandResponseModel.model_validate(response)
    )
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)

    with pytest.raises(HomeAssistantError, match=error):
        await button.async_press()
    vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["charging", "plugged", "chargeComplete", "none", None])
async def test_charge_now_availability_depends_on_charging_status(hass, status) -> None:
    """Only a confirmed connected charger enables the button."""
    vehicle = _Vehicle(type_="electric")
    vehicle.dashboard.charging_status = status
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    button.coordinator.data[0]["is_cached"] = True

    assert button.available is (status in ("plugged", "chargeComplete"))

    button.coordinator.last_update_success = False
    assert not button.available


@pytest.mark.asyncio
async def test_charge_now_availability_updates_with_coordinator(hass) -> None:
    """Polling reflects app-started charging without a local button press."""
    vehicle = _Vehicle(type_="electric")
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    button.coordinator.data[0]["is_cached"] = True
    button.async_write_ha_state = Mock()
    assert button.available

    unsubscribe = button.coordinator.async_add_listener(button._handle_coordinator_update)
    try:
        for status, level, expected in [
            ("charging", 80, False),
            ("chargeComplete", 100, False),
            ("chargeComplete", 80, True),
            ("none", 80, False),
            ("plugged", 80, True),
        ]:
            updated_vehicle = _Vehicle(type_="electric")
            updated_vehicle.dashboard.charging_status = status
            updated_vehicle.dashboard.battery_level = level
            button.coordinator.async_set_updated_data(
                [{**button.coordinator.data[0], "data": updated_vehicle}]
            )
            assert button.available is expected
    finally:
        unsubscribe()

    assert button.async_write_ha_state.call_count == 5
    assert button._charge_refresh_task is None
    vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_now_rejects_press_when_already_charging(hass) -> None:
    """Direct presses cannot send redundant commands while charging."""
    vehicle = _Vehicle(type_="electric")
    vehicle.dashboard.charging_status = "charging"
    vehicle.send_next_charging_command = AsyncMock()
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)

    with pytest.raises(HomeAssistantError, match="already charging"):
        await button.async_press()

    vehicle.send_next_charging_command.assert_not_awaited()
    vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["none", None, "unknown"])
async def test_charge_now_rejects_press_without_charger(hass, status) -> None:
    """Disconnected or unknown status cannot send a charge-now command."""
    vehicle = _Vehicle(type_="electric")
    vehicle.dashboard.charging_status = status
    vehicle.send_next_charging_command = AsyncMock()
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)

    with pytest.raises(HomeAssistantError, match="no confirmed charger connection"):
        await button.async_press()

    vehicle.send_next_charging_command.assert_not_awaited()
    vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "expected"),
    [(0, True), (80, True), (99.9, True), (100, False), (None, False)],
)
async def test_charge_complete_availability_depends_on_percentage(
    hass, level, expected
) -> None:
    """Completed charging permits a restart only with known remaining capacity."""
    vehicle = _Vehicle(type_="electric")
    vehicle.dashboard.charging_status = "chargeComplete"
    vehicle.dashboard.battery_level = level
    vehicle.send_next_charging_command = AsyncMock()
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)
    button.coordinator.data[0]["is_cached"] = True

    assert button.available is expected
    if not expected:
        with pytest.raises(HomeAssistantError, match="battery capacity remaining"):
            await button.async_press()
        vehicle.send_next_charging_command.assert_not_awaited()
        vehicle.refresh_electric_realtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_now_button_reports_transport_error(hass) -> None:
    """Transport failures do not look like successful button presses."""
    vehicle = _Vehicle(type_="electric")
    vehicle.send_next_charging_command = AsyncMock(side_effect=RuntimeError("failed"))
    button = _button(hass, ToyotaChargeNowButton, vehicle, CHARGE_NOW_BUTTON_DESCRIPTION)

    with pytest.raises(HomeAssistantError, match="could not send the charge-now command"):
        await button.async_press()
    vehicle.refresh_electric_realtime_status.assert_not_awaited()
