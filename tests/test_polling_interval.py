"""Tests for the configurable polling interval, including disabling it."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
from pytoyoda.models.vehicle import Vehicle

from custom_components.toyota.const import (
    CONF_METRIC_VALUES,
    CONF_POLLING_INTERVAL_MINUTES,
    DOMAIN,
)


def _vehicle_guid_payload(vin: str = "SB1TESTVIN00000001") -> dict[str, Any]:
    """Return a minimal raw `/v2/vehicle/guid` payload."""
    return {
        "alerts": [],
        "brand": "T",
        "capabilities": [],
        "evVehicle": False,
        "extendedCapabilities": {
            "batteryStatus": False,
            "econnectVehicleStatusCapable": False,
            "lastParkedCapable": True,
            "telemetryCapable": True,
            "vehicleStatus": True,
        },
        "features": {
            "lastParked": True,
            "serviceHistory": True,
            "telemetry": True,
            "vehicleStatus": True,
        },
        "fuelType": "B",
        "nickName": "Test Toyota",
        "services": [],
        "subscriptions": [],
        "vin": vin,
    }


def _fake_api():
    """Return an API stub with the callables Vehicle.__init__ expects."""

    async def _noop(*_args, **_kwargs):
        return None

    return SimpleNamespace(
        get_climate_settings=_noop,
        get_climate_status=_noop,
        get_location=_noop,
        get_notifications=_noop,
        get_remote_status=_noop,
        get_service_history=_noop,
        get_telemetry=_noop,
        get_trips=_noop,
        get_vehicle_electric_status=_noop,
        get_vehicle_health_status=_noop,
    )


def _fake_client_factory():
    """Return a MyT replacement whose get_vehicles() returns one live vehicle."""

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self._api = _fake_api()

        async def login(self) -> None:
            return None

        async def get_vehicles(self):
            return [
                Vehicle(
                    self._api,
                    VehicleGuidModel.model_validate(_vehicle_guid_payload()),
                    metric=True,
                )
            ]

    return _FakeClient


async def _noop_update(self, **_kwargs) -> None:
    """Test double for Vehicle.update()."""
    return


async def _noop_summary(self):
    """Test double for Vehicle summary fetchers."""
    return


def _setup_common_mocks(hass, monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.toyota.MyT",
        _fake_client_factory(),
    )
    monkeypatch.setattr(
        "custom_components.toyota._async_register_services", AsyncMock()
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(Vehicle, "update", _noop_update)
    monkeypatch.setattr(Vehicle, "get_current_day_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_week_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_month_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_year_summary", _noop_summary)


@pytest.mark.asyncio
async def test_polling_interval_zero_disables_automatic_polling(hass, monkeypatch):
    """A polling interval of 0 should disable the coordinator's auto-refresh."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "email": "test@example.com",
            "password": "password",
            CONF_METRIC_VALUES: True,
        },
        options={CONF_POLLING_INTERVAL_MINUTES: 0},
        entry_id="entry_polling_disabled",
        title="Toyota test",
    )
    entry.add_to_hass(hass)
    _setup_common_mocks(hass, monkeypatch)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.update_interval is None
    # The initial fetch still runs even with automatic polling disabled.
    assert coordinator.data is not None


@pytest.mark.asyncio
async def test_polling_interval_positive_sets_update_interval(hass, monkeypatch):
    """A positive polling interval should configure the coordinator normally."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "email": "test@example.com",
            "password": "password",
            CONF_METRIC_VALUES: True,
        },
        options={CONF_POLLING_INTERVAL_MINUTES: 120},
        entry_id="entry_polling_120",
        title="Toyota test",
    )
    entry.add_to_hass(hass)
    _setup_common_mocks(hass, monkeypatch)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.update_interval == timedelta(minutes=120)
