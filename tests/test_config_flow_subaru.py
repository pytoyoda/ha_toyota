"""Tests for the Subaru brand option (pytoyoda brand code "S")."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.models.endpoints.vehicle_guid import VehicleGuidModel
from pytoyoda.models.vehicle import Vehicle

from custom_components.toyota.config_flow import BRAND_API_MAP, BRAND_OPTIONS
from custom_components.toyota.const import (
    CONF_BRAND,
    CONF_BRAND_MAPPING,
    CONF_METRIC_VALUES,
    DOMAIN,
)
from tests.test_polling_interval import _fake_api, _vehicle_guid_payload


def _recording_client_factory(captured: dict[str, Any]):
    """Return a MyT replacement that records its constructor kwargs."""

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)
            self._api = _fake_api()

        async def login(self) -> None:
            return None

        async def get_vehicles(self) -> list[Vehicle]:
            payload = _vehicle_guid_payload(vin="JF1TESTSOLTERRA001")
            payload["brand"] = "S"
            return [
                Vehicle(
                    self._api, VehicleGuidModel.model_validate(payload), metric=True
                )
            ]

    return _FakeClient


def test_subaru_brand_maps_to_api_code_s() -> None:
    """Subaru must be selectable and map to pytoyoda brand code "S"."""
    assert BRAND_OPTIONS["subaru"] == "Subaru"
    assert BRAND_API_MAP["subaru"] == "S"
    assert CONF_BRAND_MAPPING["S"] == "Subaru"


@pytest.mark.asyncio
async def test_config_flow_subaru_passes_brand_code_s(hass, monkeypatch) -> None:
    """Selecting Subaru must log in with brand "S" and create a Subaru entry."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "custom_components.toyota.config_flow.MyT",
        _recording_client_factory(captured),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_BRAND: "subaru",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Subaru - user@example.com"
    assert result["data"][CONF_BRAND] == "subaru"
    assert result["result"].unique_id == "user@example.com_subaru"
    assert captured["brand"] == "S"


@pytest.mark.asyncio
async def test_setup_entry_subaru_passes_brand_code_s(hass, monkeypatch) -> None:
    """A stored Subaru entry must construct the pytoyoda client with brand "S"."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "custom_components.toyota.MyT", _recording_client_factory(captured)
    )
    monkeypatch.setattr(
        "custom_components.toyota._async_register_services", AsyncMock()
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())

    async def _noop(self, **_kwargs: Any) -> None:
        return None

    for name in (
        "update",
        "get_current_day_summary",
        "get_current_week_summary",
        "get_current_month_summary",
        "get_current_year_summary",
    ):
        monkeypatch.setattr(Vehicle, name, _noop)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BRAND: "subaru",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True,
        },
        entry_id="entry_subaru",
        title="Subaru - user@example.com",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    assert captured["brand"] == "S"
