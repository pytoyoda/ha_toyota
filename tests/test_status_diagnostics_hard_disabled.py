"""Regression test: status diagnostics must advance even when HARD_DISABLED.

When smart status-refresh is user-disabled (``enable_status_refresh=False``)
or auto-disabled, the coordinator still fetches ``/status`` every cycle via
the legacy fallback path in ``_enact_decision``. Before this fix that legacy
fetch was a bare ``vehicle.update(only=["status"])`` with no bookkeeping, so
``status_last_reported_by_car`` (``last_status_occurrence_date_per_vin``)
and ``last_status_fetch_at`` stayed ``None`` forever - even though the
underlying data kept refreshing fine every cycle. The fix routes the legacy
fetch through ``_execute_get_only``, which already carries this bookkeeping
for the GET_ONLY action.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
from pytoyoda.models.vehicle import Vehicle

from custom_components.toyota.const import (
    CONF_ENABLE_STATUS_REFRESH,
    CONF_METRIC_VALUES,
    DOMAIN,
)

FAKE_OCCURRENCE = datetime(2026, 9, 21, 5, 1, 38, tzinfo=timezone.utc)


def _vehicle_guid_payload(vin: str = "SB1TESTVIN00000003") -> dict[str, Any]:
    """Return a minimal raw `/v2/vehicle/guid` payload."""
    return {
        "alerts": [],
        "brand": "T",
        "capabilities": [],
        "evVehicle": False,
        "extendedCapabilities": {
            "econnectVehicleStatusCapable": False,
            "telemetryCapable": True,
            "vehicleStatus": True,
        },
        "features": {
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


async def _update_sets_status_on_only(self, **kwargs) -> None:
    """Test double for Vehicle.update(): populate `status` when asked for it.

    Mirrors a real cycle where the legacy HARD_DISABLED path still fetches
    /status successfully every time - the bug was that nothing read
    occurrence_date back out of it, not that the fetch itself failed.
    """
    if kwargs.get("only") == ["status"]:
        self._endpoint_data["status"] = SimpleNamespace(
            payload=SimpleNamespace(occurrence_date=FAKE_OCCURRENCE)
        )


async def _noop_summary(self):
    """Test double for Vehicle summary fetchers."""
    return


def _setup_common_mocks(hass, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.toyota.MyT", _fake_client_factory())
    monkeypatch.setattr(
        "custom_components.toyota._async_register_services", AsyncMock()
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(Vehicle, "update", _update_sets_status_on_only)
    monkeypatch.setattr(Vehicle, "get_current_day_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_week_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_month_summary", _noop_summary)
    monkeypatch.setattr(Vehicle, "get_current_year_summary", _noop_summary)


@pytest.mark.asyncio
async def test_hard_disabled_user_still_advances_status_diagnostics(
    hass, monkeypatch
) -> None:
    """enable_status_refresh=False (HARD_DISABLED_USER) must still record

    status_last_reported_by_car / last_successful status fetch, since the
    legacy path keeps fetching /status successfully every cycle.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "email": "test@example.com",
            "password": "password",
            CONF_METRIC_VALUES: True,
        },
        options={CONF_ENABLE_STATUS_REFRESH: False},
        entry_id="entry_hard_disabled_user",
        title="Toyota test",
    )
    entry.add_to_hass(hass)
    _setup_common_mocks(hass, monkeypatch)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    vin = coordinator.data[0]["data"].vin

    assert coordinator._diag_status_refresh_state_per_vin[vin] == "hard_disabled_user"
    assert coordinator._diag_status_occurrence_per_vin.get(vin) == FAKE_OCCURRENCE
