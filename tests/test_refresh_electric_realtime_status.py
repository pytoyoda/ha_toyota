"""Unit tests for the refresh_electric_realtime_status service helpers.

These are pure/near-pure helpers extracted from
_async_register_services() in __init__.py, so they're tested directly
without needing a full hass/coordinator setup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.toyota import (
    ELECTRIC_REALTIME_WAKE_TIMEOUT_S,
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
    """Stand-in for pytoyoda's Vehicle, covering the post-wake poll loop.

    ``timestamps`` models what ``electric_status.last_update_timestamp``
    reports over time: index 0 is the baseline read before any polling
    ``update()`` call, and each subsequent entry is what it reports after
    the matching ``update()`` call. Defaults to a single ``None`` (never
    advances) so a test opts in to an advancing timestamp explicitly.
    """

    def __init__(
        self,
        vin: str,
        *,
        raises: bool = False,
        timestamps: list[datetime | None] | None = None,
    ) -> None:
        self.vin = vin
        self._raises = raises
        self.refresh_calls = 0
        self.update_calls = 0
        self._timestamps = timestamps if timestamps is not None else [None]
        self._timestamp_index = 0

    @property
    def electric_status(self) -> SimpleNamespace:
        return SimpleNamespace(
            last_update_timestamp=self._timestamps[self._timestamp_index]
        )

    async def refresh_electric_realtime_status(self) -> None:
        self.refresh_calls += 1
        if self._raises:
            raise RuntimeError("boom")

    async def update(self, *, only: list[str] | None = None) -> None:  # noqa: ARG002
        self.update_calls += 1
        if self._timestamp_index < len(self._timestamps) - 1:
            self._timestamp_index += 1


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch: pytest.MonkeyPatch):
    """Replace real waiting with an instantly-advancing fake clock.

    The poll loop under test sleeps in real seconds between polls; without
    this, exercising its timeout path would make the test suite actually
    wait ~25s. Patching ``asyncio.sleep`` to advance a fake clock (read via
    the patched ``dt_util.now``) keeps the loop's logic intact while making
    the test instantaneous.
    """
    clock = {"now": datetime(2024, 1, 1, tzinfo=UTC)}

    async def _fake_sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    monkeypatch.setattr("custom_components.toyota.asyncio.sleep", _fake_sleep)
    monkeypatch.setattr("custom_components.toyota.dt_util.now", lambda: clock["now"])
    return clock


async def test_wake_vehicle_calls_refresh_for_matching_vin() -> None:
    vehicle = _FakeVehicle(
        "VIN123",
        timestamps=[None, datetime(2024, 1, 1, tzinfo=UTC)],
    )
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
    # A failed wake POST must not trigger the post-wake poll loop.
    assert vehicle.update_calls == 0


async def test_wake_vehicle_polls_until_timestamp_advances() -> None:
    """The core ha_toyota#431 fix: wait for a fresh SoC before returning."""
    vehicle = _FakeVehicle(
        "VIN123",
        timestamps=[
            None,
            None,
            datetime(2024, 1, 1, tzinfo=UTC),
        ],
    )
    vehicle_data = [{"data": vehicle}]

    await _wake_vehicle_electric_realtime_status(vehicle_data, "VIN123")

    # Two polls before the timestamp advances on the third read.
    assert vehicle.update_calls == 2


async def test_wake_vehicle_polls_until_timestamp_strictly_advances() -> None:
    """A stale-but-present timestamp must not be mistaken for a fresh one."""
    baseline = datetime(2024, 1, 1, tzinfo=UTC)
    vehicle = _FakeVehicle(
        "VIN123",
        timestamps=[baseline, baseline, baseline + timedelta(minutes=1)],
    )
    vehicle_data = [{"data": vehicle}]

    await _wake_vehicle_electric_realtime_status(vehicle_data, "VIN123")

    assert vehicle.update_calls == 2


async def test_wake_vehicle_gives_up_after_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the timestamp never advances, stop polling once the budget expires."""
    vehicle = _FakeVehicle("VIN123")  # timestamps default to [None] forever.
    vehicle_data = [{"data": vehicle}]

    await _wake_vehicle_electric_realtime_status(vehicle_data, "VIN123")

    assert vehicle.update_calls >= 1
    assert f"{ELECTRIC_REALTIME_WAKE_TIMEOUT_S}" in caplog.text
