"""Regression tests for the ha_toyota#87 null-rendering fix.

Before the fix, `not getattr(getattr(...), 'closed', None)` evaluated to
`not None` -> True when the field was missing, causing every door/window/
hood/lock to render as "open" or "unlocked" on first cold cache. After the
fix, _inv_or_none() preserves None so HA renders "unknown" instead.
"""

from __future__ import annotations

from custom_components.toyota.binary_sensor import (
    HOOD_STATUS_ENTITY_DESCRIPTION,
    _inv_or_none,
)


class _FakeVehicle:
    """Minimal vehicle stub. Subclasses set lock_status to control fixtures."""

    lock_status = None


class _FakeHood:
    def __init__(self, closed: bool | None) -> None:
        self.closed = closed


class _FakeLockStatus:
    def __init__(self, hood: _FakeHood | None) -> None:
        self.hood = hood


def test_inv_or_none_preserves_none():
    assert _inv_or_none(None) is None


def test_inv_or_none_inverts_booleans():
    assert _inv_or_none(True) is False
    assert _inv_or_none(False) is True


def test_hood_value_fn_returns_none_when_lock_status_missing():
    """Cold cache: vehicle.lock_status is None -> sensor reads as unknown."""
    vehicle = _FakeVehicle()
    assert HOOD_STATUS_ENTITY_DESCRIPTION.value_fn(vehicle) is None


def test_hood_value_fn_returns_none_when_closed_field_missing():
    """Partial response: hood object exists but closed field is None."""
    vehicle = _FakeVehicle()
    vehicle.lock_status = _FakeLockStatus(hood=_FakeHood(closed=None))
    assert HOOD_STATUS_ENTITY_DESCRIPTION.value_fn(vehicle) is None


def test_hood_value_fn_inverts_closed_bool():
    """When the field IS populated, render the inverted bool (HA DOOR class
    treats True as 'open')."""
    vehicle = _FakeVehicle()
    vehicle.lock_status = _FakeLockStatus(hood=_FakeHood(closed=True))
    assert HOOD_STATUS_ENTITY_DESCRIPTION.value_fn(vehicle) is False  # closed -> not-open

    vehicle.lock_status = _FakeLockStatus(hood=_FakeHood(closed=False))
    assert HOOD_STATUS_ENTITY_DESCRIPTION.value_fn(vehicle) is True  # open
