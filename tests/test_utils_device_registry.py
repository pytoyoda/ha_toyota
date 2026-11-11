"""Tests for the device-registry compatibility helpers."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.utils import (
    device_config_entry_ids,
    get_vehicle_device,
)


class _NewRegistry:
    """Registry exposing the per-config-entry lookup (HA 2026.11+)."""

    def __init__(self, device: object) -> None:
        self.device = device
        self.calls: list[tuple[tuple[str, str], str]] = []

    def async_get_device_by_identifier(
        self, identifier: tuple[str, str], config_entry_id: str
    ) -> object:
        self.calls.append((identifier, config_entry_id))
        return self.device


class _OldRegistry:
    """Registry exposing only the legacy lookup."""

    def __init__(self, device: object) -> None:
        self.device = device
        self.calls: list[set[tuple[str, str]]] = []

    def async_get_device(self, identifiers: set[tuple[str, str]]) -> object:
        self.calls.append(identifiers)
        return self.device


def test_get_vehicle_device_uses_per_entry_lookup_when_available() -> None:
    device = object()
    reg = _NewRegistry(device)
    assert get_vehicle_device(reg, "entry-1", "VIN1") is device  # type: ignore[arg-type]
    assert reg.calls == [((DOMAIN, "VIN1"), "entry-1")]


def test_get_vehicle_device_falls_back_to_legacy_lookup() -> None:
    device = object()
    reg = _OldRegistry(device)
    assert get_vehicle_device(reg, "entry-1", "VIN1") is device  # type: ignore[arg-type]
    assert reg.calls == [{(DOMAIN, "VIN1")}]


def test_device_config_entry_ids_prefers_config_entry_id() -> None:
    device = SimpleNamespace(config_entry_id="a", config_entries={"b"})
    assert device_config_entry_ids(device) == {"a"}  # type: ignore[arg-type]


def test_device_config_entry_ids_falls_back_to_config_entries() -> None:
    device = SimpleNamespace(config_entries={"b", "c"})
    assert device_config_entry_ids(device) == {"b", "c"}  # type: ignore[arg-type]
