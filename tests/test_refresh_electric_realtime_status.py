"""Unit tests for the refresh_electric_realtime_status service helpers.

These are pure/near-pure helpers extracted from
_async_register_services() in __init__.py, so they're tested directly
without needing a full hass/coordinator setup.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.toyota import (
    _extract_device_ids,
    _wake_vehicle_electric_realtime_status,
)


class _FakeCall:
    """Minimal stand-in for homeassistant.core.ServiceCall."""

    def __init__(self, data: dict) -> None:
        self.data = data


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"device_id": "device-1"}, ["device-1"]),
        ({"device_id": ["device-1", "device-2"]}, ["device-1", "device-2"]),
        ({}, []),
        ({"device_id": None}, []),
        ({"device_id": []}, []),
    ],
)
def test_extract_device_ids(data: dict, expected: list[str]) -> None:
    assert _extract_device_ids(_FakeCall(data)) == expected


class _FakeVehicle:
    def __init__(self, vin: str, *, raises: bool = False) -> None:
        self.vin = vin
        self._raises = raises
        self.refresh_calls = 0

    async def refresh_electric_realtime_status(self) -> None:
        self.refresh_calls += 1
        if self._raises:
            raise RuntimeError("boom")


async def test_wake_vehicle_calls_refresh_for_matching_vin() -> None:
    vehicle = _FakeVehicle("VIN123")
    vehicle_data = [{"data": vehicle}]

    await _wake_vehicle_electric_realtime_status(vehicle_data, "VIN123")

    assert vehicle.refresh_calls == 1


async def test_wake_vehicle_skips_unknown_vin() -> None:
    vehicle = _FakeVehicle("VIN123")
    vehicle_data = [{"data": vehicle}]

    await _wake_vehicle_electric_realtime_status(vehicle_data, "OTHER-VIN")

    assert vehicle.refresh_calls == 0


async def test_wake_vehicle_ignores_entries_without_data() -> None:
    vehicle_data = [{"data": None}, SimpleNamespace(get=lambda *_a: None)]

    # Should not raise even though neither entry has a matching vin.
    await _wake_vehicle_electric_realtime_status(vehicle_data, "VIN123")


async def test_wake_vehicle_swallows_refresh_errors() -> None:
    vehicle = _FakeVehicle("VIN123", raises=True)
    vehicle_data = [{"data": vehicle}]

    # Should not propagate the exception - failures are logged and swallowed
    # so one bad VIN doesn't abort refreshes for the rest of the fleet.
    await _wake_vehicle_electric_realtime_status(vehicle_data, "VIN123")

    assert vehicle.refresh_calls == 1
