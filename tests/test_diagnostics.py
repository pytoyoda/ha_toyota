"""Exercise diagnostics downloads with synthetic, offline vehicle data."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

from pydantic import BaseModel
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toyota.const import DOMAIN
from custom_components.toyota.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)

VIN_A = "JTDBR32E720000001"
VIN_B = "JTDBR32E720000002"
REDACTED = "**REDACTED**"


@pytest.fixture
def diagnostics_context(hass):
    """Install only the state needed by the public download entry points."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry",
        title="driver@example.com",
        unique_id="driver@example.com",
        data={"email": "driver@example.com", "password": "test-password"},
        options={"metric_values": True, "password": "option-password"},
    )
    coordinator = SimpleNamespace(
        data=[],
        last_update_success=True,
        last_exception=None,
        update_interval=timedelta(minutes=5),
    )
    bucket = {}
    hass.data[DOMAIN] = {entry.entry_id: coordinator, "entry_diag": bucket}
    return entry, coordinator, bucket


async def test_entry_redacts_nested_payloads_without_mutating_sources(
    hass, diagnostics_context
):
    """Account secrets and nested endpoint data stay out of the download."""
    entry, coordinator, bucket = diagnostics_context
    payload = {
        "vehicle_info": {"vin": VIN_A, "Email": "owner@example.com"},
        "status": [
            {"LATITUDE": 0, "longitude": 24.5, "contract_id": "contract-secret"},
            {"imei": None, "subscriptionId": "subscription-secret"},
        ],
        f"vehicle/{VIN_A}": f"Refresh {VIN_A} at /vehicles/{VIN_A}",
        "battery": 0,
        "connected": False,
    }
    original = deepcopy(payload)
    coordinator.data = [
        {"data": SimpleNamespace(vin=VIN_A, _dump_all=Mock(return_value=payload))}
    ]
    bucket["last_error_per_vin"] = {VIN_A: f"Retry {VIN_A}"}

    exported = await async_get_config_entry_diagnostics(hass, entry)

    dump = exported["vehicles"][0]["dump"]
    token = dump["vehicle_info"]["vin"]
    assert token.startswith("vin:")
    assert dump["vehicle_info"]["Email"] == REDACTED
    assert dump["status"] == [
        {"LATITUDE": REDACTED, "longitude": REDACTED, "contract_id": REDACTED},
        {"imei": None, "subscriptionId": REDACTED},
    ]
    assert dump[f"vehicle/{token}"] == f"Refresh {token} at /vehicles/{token}"
    assert dump["battery"] == 0
    assert dump["connected"] is False
    assert exported["diagnostics_state"]["last_error_per_vin"] == {
        token: f"Retry {token}"
    }
    assert exported["entry"]["data"] == {"email": REDACTED, "password": REDACTED}
    assert exported["entry"]["options"] == {
        "metric_values": True,
        "password": REDACTED,
    }
    assert "title" not in exported["entry"]
    assert "unique_id" not in exported["entry"]
    encoded = json.dumps(exported)
    for secret in (
        VIN_A,
        "driver@example.com",
        "owner@example.com",
        "test-password",
        "option-password",
        "contract-secret",
        "subscription-secret",
    ):
        assert secret not in encoded
    assert payload == original
    assert bucket["last_error_per_vin"] == {VIN_A: f"Retry {VIN_A}"}
    assert entry.data["password"] == "test-password"
    assert entry.options["password"] == "option-password"


@pytest.mark.parametrize("include_toyota_identifier", [True, False])
async def test_device_scopes_vehicles_and_cached_state(
    hass, diagnostics_context, include_toyota_identifier
):
    """Only matching Toyota identifiers select vehicles and per-VIN caches."""
    entry, coordinator, bucket = diagnostics_context
    vehicles = [
        SimpleNamespace(vin=vin, _dump_all=Mock(return_value={"vin": vin}))
        for vin in (VIN_A, VIN_B)
    ]
    coordinator.data = [{"data": None}, *({"data": v} for v in vehicles)]
    bucket.update(
        last_good_per_vin=dict(zip((VIN_A, VIN_B), vehicles)),
        failures_per_vin={VIN_A: 1, VIN_B: 2},
        refresh_in_progress=False,
    )
    identifiers = {("other_integration", VIN_A), ("other_integration", VIN_B)}
    if include_toyota_identifier:
        identifiers.add((DOMAIN, VIN_A))
    device = SimpleNamespace(identifiers=identifiers, name=f"Car {VIN_A}", model="Test")

    all_data = await async_get_config_entry_diagnostics(hass, entry)
    exported = await async_get_device_diagnostics(hass, entry, device)

    token_a = all_data["vehicles"][1]["dump"]["vin"]
    token_b = all_data["vehicles"][2]["dump"]["vin"]
    assert token_a != token_b
    assert all_data["diagnostics_state"]["last_good_per_vin"] == {
        token_a: True,
        token_b: True,
    }
    assert exported["vehicles"] == (
        [all_data["vehicles"][1]] if include_toyota_identifier else []
    )
    assert exported["diagnostics_state"] == {
        "last_good_per_vin": {token_a: True} if include_toyota_identifier else {},
        "failures_per_vin": {token_a: 1} if include_toyota_identifier else {},
        "refresh_in_progress": False,
    }
    assert exported["device"] == {"name": f"Car {token_a}", "model": "Test"}
    assert "entry" not in exported
    assert VIN_A not in json.dumps(exported)
    assert VIN_B not in json.dumps(exported)
    assert bucket["last_good_per_vin"][VIN_A] is vehicles[0]
    assert bucket["failures_per_vin"] == {VIN_A: 1, VIN_B: 2}


async def test_entry_serializes_non_json_bucket_values(hass, diagnostics_context):
    """Dates, containers and Pydantic models produce a JSON-encodable download."""

    class Status(BaseModel):
        updated_at: datetime
        enabled: bool

    entry, _, bucket = diagnostics_context
    timestamp = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    status = Status(updated_at=timestamp, enabled=False)
    bucket.update(
        timestamp=timestamp,
        day=date(2026, 10, 1),
        attempts=(1, None, False, 2.5),
        flags={"ready", "cached"},
        counters={7: 0},
        status=status,
    )

    exported = await async_get_config_entry_diagnostics(hass, entry)

    state = json.loads(json.dumps(exported))["diagnostics_state"]
    assert state["timestamp"] == "2026-10-01T12:30:00+00:00"
    assert state["day"] == "2026-10-01"
    assert state["attempts"] == [1, None, False, 2.5]
    assert sorted(state["flags"]) == ["cached", "ready"]
    assert state["counters"] == {"7": 0}
    assert state["status"] == {"updated_at": "2026-10-01T12:30:00Z", "enabled": False}
    assert bucket["timestamp"] is timestamp
    assert bucket["status"] is status
    assert bucket["attempts"] == (1, None, False, 2.5)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(object(), id="unknown-object"),
        pytest.param(Mock(model_dump_json=Mock(return_value="{")), id="invalid-json"),
        pytest.param(
            Mock(model_dump_json=Mock(side_effect=ValueError("cannot serialize"))),
            id="model-error",
        ),
    ],
)
async def test_entry_preserves_download_when_bucket_value_cannot_serialize(
    hass, diagnostics_context, value
):
    """One unsupported value falls back to text without losing other state."""
    entry, _, bucket = diagnostics_context
    bucket.update(unsupported=value, attempts=3)

    exported = await async_get_config_entry_diagnostics(hass, entry)

    assert json.loads(json.dumps(exported))["diagnostics_state"] == {
        "unsupported": repr(value),
        "attempts": 3,
    }


async def test_entry_bounds_recursive_bucket_values(hass, diagnostics_context):
    """A recursive cache value cannot cause the entire download to recurse forever."""
    entry, _, bucket = diagnostics_context
    recursive = {}
    recursive["child"] = recursive
    bucket["recursive"] = recursive

    exported = await async_get_config_entry_diagnostics(hass, entry)

    assert "<max-depth>" in json.dumps(exported)
    assert recursive["child"] is recursive


async def test_entry_exports_vehicle_summary_and_coordinator_health(
    hass, diagnostics_context
):
    """Summaries expose true capabilities, timestamps and coordinator failures."""
    entry, coordinator, _ = diagnostics_context
    features = SimpleNamespace(
        model_dump=Mock(
            return_value={
                "z_enabled": True,
                "a_enabled": True,
                "disabled": False,
                "number": 1,
            }
        )
    )
    ext = SimpleNamespace(
        climate_capable=True,
        climate_temperature_control_full=True,
        model_dump=Mock(return_value={"climate_capable": True}),
    )
    info = SimpleNamespace(
        car_model_name="Test car",
        features=features,
        extended_capabilities=ext,
        remote_service_capabilities=None,
        remote_display=7,
    )
    vehicle = SimpleNamespace(
        vin=VIN_A, _vehicle_info=info, _dump_all=Mock(return_value={"battery": 80})
    )
    coordinator.data = [
        {
            "data": vehicle,
            "metric_values": True,
            "is_cached": True,
            "last_successful_fetch": datetime(2026, 10, 1, 12, tzinfo=UTC),
            "last_error_time": datetime(2026, 10, 1, 13, tzinfo=UTC),
            "last_error_code": "timeout",
            "statistics": {},
        }
    ]
    coordinator.last_update_success = False
    coordinator.last_exception = TimeoutError("Toyota unavailable")

    exported = await async_get_config_entry_diagnostics(hass, entry)

    result = exported["vehicles"][0]
    summary = result["summary"]
    assert summary["car_model_name"] == "Test car"
    assert summary["capabilities_true"] == {
        "features": ["a_enabled", "z_enabled"],
        "extended_capabilities": ["climate_capable"],
        "remote_service_capabilities": None,
    }
    assert summary["remote_display"] == {"raw": 7, "decoded": "ACTIVATED"}
    assert summary["predicted_climate_class"]["class"] == "FULL_CLIMATE"
    assert result["dump"] == {"battery": 80}
    assert result["metric_values"] is True
    assert result["is_cached"] is True
    assert result["last_successful_fetch"] == "2026-10-01T12:00:00+00:00"
    assert result["last_error_time"] == "2026-10-01T13:00:00+00:00"
    assert result["last_error_code"] == "timeout"
    assert result["has_statistics"] is True
    assert exported["coordinator"] == {
        "last_update_success": False,
        "last_exception": "Toyota unavailable",
        "update_interval": "0:05:00",
        "vehicle_count": 1,
    }
    assert all(exported["meta"]["accessors"].values())


async def test_entry_isolates_unavailable_vehicle_data(hass, diagnostics_context):
    """Missing and failing accessors leave useful data from other cars intact."""
    entry, coordinator, _ = diagnostics_context
    broken_flags = SimpleNamespace(model_dump=Mock(side_effect=ValueError("bad flags")))
    broken = SimpleNamespace(
        vin=VIN_A,
        _vehicle_info=SimpleNamespace(features=broken_flags),
        _dump_all=Mock(side_effect=RuntimeError("endpoint unavailable")),
    )
    healthy = SimpleNamespace(vin=VIN_B, _dump_all=Mock(return_value={"battery": 80}))
    coordinator.data = [{"data": None}, {"data": broken}, {"data": healthy}]

    exported = await async_get_config_entry_diagnostics(hass, entry)

    missing, failed, successful = exported["vehicles"]
    assert missing["summary"] is None
    assert missing["dump"] is None
    assert missing["has_statistics"] is False
    assert missing["last_successful_fetch"] is None
    assert failed["summary"]["capabilities_true"]["features"] is None
    assert "endpoint unavailable" in failed["dump"]["error"]
    assert successful["summary"] == {"error": "no _vehicle_info"}
    assert successful["dump"] == {"battery": 80}
    json.dumps(exported)


@pytest.mark.parametrize("vehicles", [None, []])
async def test_downloads_handle_empty_coordinator_and_missing_bucket(
    hass, diagnostics_context, vehicles
):
    """Downloads remain usable before the first vehicle refresh completes."""
    entry, coordinator, _ = diagnostics_context
    coordinator.data = vehicles
    del hass.data[DOMAIN]["entry_diag"]
    device = SimpleNamespace(identifiers={(DOMAIN, VIN_A)}, name="Car", model=None)

    entry_data = await async_get_config_entry_diagnostics(hass, entry)
    device_data = await async_get_device_diagnostics(hass, entry, device)

    for exported in (entry_data, device_data):
        assert exported["vehicles"] == []
        assert exported["diagnostics_state"] == {}
        assert exported["coordinator"]["vehicle_count"] == 0
        assert exported["coordinator"]["last_exception"] is None
        assert "accessors" not in exported["meta"]
        json.dumps(exported)
