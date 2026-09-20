"""Tests for persisted vehicle-list fallback storage and cold-start recovery."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.exceptions import ToyotaApiError
from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
from pytoyoda.models.vehicle import Vehicle

from custom_components.toyota.const import CONF_METRIC_VALUES, DOMAIN
from custom_components.toyota.refresh_strategy import (
    RefreshAction,
    RefreshDecision,
    RefreshState,
    RefreshTrigger,
)
from custom_components.toyota.vehicle_list_cache import VehicleListStore


def _vehicle_guid_payload(
    vin: str = "SB1TESTVIN00000001",
    *,
    telemetry_capable: bool = True,
    vehicle_status: bool = True,
) -> dict[str, Any]:
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
            "telemetryCapable": telemetry_capable,
            "vehicleStatus": vehicle_status,
        },
        "features": {
            "lastParked": True,
            "serviceHistory": True,
            "telemetry": telemetry_capable,
            "vehicleStatus": vehicle_status,
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


def _vehicle_from_payload(payload: dict[str, Any], *, metric: bool = True) -> Vehicle:
    """Build a real pytoyoda Vehicle from the raw GUID payload."""
    return Vehicle(_fake_api(), VehicleGuidModel.model_validate(payload), metric=metric)


def _fake_client_factory(get_vehicles_result):
    """Return a MyT replacement whose get_vehicles() returns/raises as configured."""

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self._api = _fake_api()

        async def login(self) -> None:
            return None

        async def get_vehicles(self):
            if isinstance(get_vehicles_result, BaseException):
                raise get_vehicles_result
            return get_vehicles_result

    return _FakeClient


def _serve_from_cache_decision(*_args, **_kwargs) -> RefreshDecision:
    """Keep status refresh out of the way for this focused fallback test."""
    return RefreshDecision(
        action=RefreshAction.SERVE_FROM_CACHE,
        trigger=RefreshTrigger.NONE,
        refresh_state=RefreshState.ACTIVE,
    )


async def _noop_update(self, **_kwargs) -> None:
    """Test double for Vehicle.update()."""
    return


async def _noop_summary(self):
    """Test double for Vehicle summary fetchers."""
    return


@pytest.mark.asyncio
async def test_vehicle_list_store_round_trip_rebuilds_real_vehicle(hass):
    """Saved GUID payloads should rebuild real Vehicle objects with capabilities."""
    store = VehicleListStore(hass, "entry1")
    await store.load()
    store.replace_from_vehicles([_vehicle_from_payload(_vehicle_guid_payload())])
    await store.save()

    reloaded = VehicleListStore(hass, "entry1")
    await reloaded.load()
    rebuilt = reloaded.rebuild_vehicles(_fake_api(), metric=False)

    assert len(rebuilt) == 1
    assert rebuilt[0].vin == "SB1TESTVIN00000001"
    assert reloaded.get()[0]["extendedCapabilities"]["telemetryCapable"] is True
    assert any(
        endpoint.name == "telemetry" and endpoint.capable
        for endpoint in rebuilt[0]._api_endpoints
    )
    assert any(
        endpoint.name == "status" and endpoint.capable
        for endpoint in rebuilt[0]._api_endpoints
    )


@pytest.mark.asyncio
async def test_cold_start_uses_persisted_vehicle_list_when_get_vehicles_fails(
    hass, monkeypatch, caplog
):
    """A persisted GUID cache should bridge a cold-start `/guid` outage."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "email": "test@example.com",
            "password": "password",
            CONF_METRIC_VALUES: True,
        },
        entry_id="entry1",
        title="Toyota test",
    )
    entry.add_to_hass(hass)

    store = VehicleListStore(hass, entry.entry_id)
    await store.load()
    store.set([_vehicle_guid_payload()])
    await store.save()

    monkeypatch.setattr(
        "custom_components.toyota.MyT",
        _fake_client_factory(ToyotaApiError("Request Failed. 500, boom")),
    )
    monkeypatch.setattr("custom_components.toyota.decide", _serve_from_cache_decision)
    monkeypatch.setattr(
        "custom_components.toyota._async_register_services", AsyncMock()
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(Vehicle, "update", _noop_update)
    monkeypatch.setattr(Vehicle, "get_current_day_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_week_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_month_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_year_summary", _noop_summary)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data is not None
    assert [vehicle_data["data"].vin for vehicle_data in coordinator.data] == [
        "SB1TESTVIN00000001"
    ]
    assert (
        coordinator.data[0][
            "data"
        ]._vehicle_info.extended_capabilities.telemetry_capable  # noqa: SLF001
        is True
    )
    assert "using persisted vehicle list from last successful run" in caplog.text


@pytest.mark.asyncio
async def test_cold_start_without_persisted_vehicle_list_still_fails_setup(
    hass, monkeypatch
):
    """Without a persisted cache the first-refresh failure should behave as before."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "email": "test@example.com",
            "password": "password",
            CONF_METRIC_VALUES: True,
        },
        entry_id="entry1",
        title="Toyota test",
    )
    entry.add_to_hass(hass)

    monkeypatch.setattr(
        "custom_components.toyota.MyT",
        _fake_client_factory(ToyotaApiError("Request Failed. 500, boom")),
    )
    monkeypatch.setattr(
        "custom_components.toyota._async_register_services", AsyncMock()
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())

    assert await hass.config_entries.async_setup(entry.entry_id) is False
