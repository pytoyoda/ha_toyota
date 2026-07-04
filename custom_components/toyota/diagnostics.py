"""Diagnostics support for the Toyota Connected Services integration.

Produces a one-click, auto-redacted "Download diagnostics" export — at both the
integration-entry level (all cars) and per car/device — aimed at non-contributor
testers. The payload is chosen to accelerate development: the full capability
flag responses, a decoded remote-display gate, every endpoint payload pytoyoda
last fetched, the coordinator/refresh state, and the outcome of the last remote
command per car.

Redaction is done by this module, not trusted to pytoyoda: ``Vehicle._dump_all()``
does not fully censor its nested endpoint payloads (raw VIN, GPS and contract_id
can appear), so we redact the whole assembled export ourselves:
- ``_deep_redact`` blanks sensitive keys (GPS, contract id, imei, subscription
  id, e-mail, ...) and tokenises the VIN — as dict keys, standalone values, and
  substrings (subscription ids, links, notification text) — to a stable,
  non-reversible ``vin:<hash>`` so a car stays correlatable across sections.
- ``async_redact_data`` strips the HA-side config-entry account credentials.

The module is deliberately defensive: every pytoyoda access is guarded so an API
migration degrades a single field (and is flagged in ``meta.accessors``) rather
than breaking setup or the download.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.redact import async_redact_data

from .const import DOMAIN
from .utils import decode_remote_display, predict_climate_class

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}

# Bucket keys whose values are NOT JSON-native and must not be dumped raw:
# last_good_per_vin holds live pytoyoda Vehicle objects (reduced to presence).
_BUCKET_PRESENCE_ONLY = {"last_good_per_vin"}

REDACTED = "**REDACTED**"

# Sensitive payload keys to blank. We deliberately do NOT rely on pytoyoda's
# Vehicle._dump_all() self-censoring: it does not fully censor the nested
# endpoint payloads (raw VIN, GPS latitude/longitude and contract_id can
# appear). We reuse pytoyoda's own key list (minus "vin", which is *tokenised*
# for cross-section correlation rather than blanked) and apply it ourselves over
# the whole assembled export, plus a substring VIN tokeniser for values/keys
# where the VIN is embedded (subscription ids, links, notification text).
try:
    from pytoyoda.utils.log_utils import DEFAULT_SENSITIVE_KEYS as _PT_KEYS
except Exception:  # noqa: BLE001  # pragma: no cover - defensive
    _PT_KEYS = set()
_REDACT_KEYS = (frozenset(k.lower() for k in _PT_KEYS) - {"vin"}) | {
    "email",
    "password",
    "subscription_id",
    "subscriptionid",
}


def _iso(value: Any) -> Any:
    """Best-effort ISO string for datetimes; pass everything else through."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _jsonify(obj: Any, _depth: int = 0) -> Any:
    """Recursively coerce arbitrary objects to JSON-native values.

    Handles datetimes, tuples, and pydantic models (via ``model_dump_json``).
    Any unknown non-native leaf falls back to ``repr`` so a single odd value can
    never make the whole download fail to encode.
    """
    if _depth > 8:
        return "<max-depth>"
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump_json"):  # pydantic model
        try:
            return json.loads(obj.model_dump_json())
        except Exception:  # noqa: BLE001
            return repr(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonify(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonify(v, _depth + 1) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return repr(obj)


def _true_flags(model: Any) -> list[str] | None:
    """Sorted names of the ``True`` boolean flags on a capability model."""
    if model is None:
        return None
    try:
        return sorted(k for k, v in model.model_dump().items() if v is True)
    except Exception:  # noqa: BLE001
        return None


def _vehicle_summary(vehicle: Any) -> dict[str, Any]:
    """Derived at-a-glance signals off ``_vehicle_info`` — static, no network."""
    info = getattr(vehicle, "_vehicle_info", None)  # noqa: SLF001
    if info is None:
        return {"error": "no _vehicle_info"}

    remote_display = getattr(info, "remote_display", None)
    klass, hint = predict_climate_class(
        getattr(info, "features", None),
        getattr(info, "extended_capabilities", None),
    )
    return {
        "car_model_name": getattr(info, "car_model_name", None),
        "capabilities_true": {
            "features": _true_flags(getattr(info, "features", None)),
            "extended_capabilities": _true_flags(
                getattr(info, "extended_capabilities", None)
            ),
            "remote_service_capabilities": _true_flags(
                getattr(info, "remote_service_capabilities", None)
            ),
        },
        "remote_display": {
            "raw": _jsonify(remote_display),
            "decoded": decode_remote_display(remote_display),
        },
        "predicted_climate_class": {"class": klass, "hint": hint},
    }


def _dump_vehicle(vehicle: Any) -> Any:
    """Full censored endpoint dump; degrade to a marker rather than fail."""
    if vehicle is None:
        return None
    try:
        return vehicle._dump_all()  # noqa: SLF001
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Vehicle._dump_all() failed: %s", err)
        return {"error": f"_dump_all unavailable: {err!r}"}


def _serialize_vehicle(vd: dict[str, Any]) -> dict[str, Any]:
    """One coordinator ``VehicleData`` entry as JSON-native diagnostics data."""
    vehicle = vd.get("data")
    return {
        "summary": _vehicle_summary(vehicle) if vehicle is not None else None,
        "dump": _dump_vehicle(vehicle),
        "metric_values": vd.get("metric_values"),
        "is_cached": vd.get("is_cached"),
        "last_successful_fetch": _iso(vd.get("last_successful_fetch")),
        "last_error_time": _iso(vd.get("last_error_time")),
        "last_error_code": vd.get("last_error_code"),
        "has_statistics": vd.get("statistics") is not None,
    }


def _coordinator_block(coordinator: DataUpdateCoordinator) -> dict[str, Any]:
    """Coordinator health at a glance."""
    last_exc = getattr(coordinator, "last_exception", None)
    return {
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "last_exception": str(last_exc) if last_exc else None,
        "update_interval": str(getattr(coordinator, "update_interval", None)),
        "vehicle_count": len(coordinator.data) if coordinator.data else 0,
    }


def _pytoyoda_meta(coordinator: DataUpdateCoordinator) -> dict[str, Any]:
    """Loaded pytoyoda version + which accessors resolved.

    Makes the export self-describing: an API migration shows up as a flipped
    flag here instead of a silently broken dump.
    """
    meta: dict[str, Any] = {}
    try:
        import pytoyoda  # noqa: PLC0415

        meta["pytoyoda_version"] = getattr(pytoyoda, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        meta["pytoyoda_version"] = "import-failed"

    sample = next(
        (vd["data"] for vd in (coordinator.data or []) if vd.get("data") is not None),
        None,
    )
    if sample is not None:
        info = getattr(sample, "_vehicle_info", None)  # noqa: SLF001
        meta["accessors"] = {
            "dump_all": hasattr(sample, "_dump_all"),
            "vehicle_info": info is not None,
            "features": hasattr(info, "features"),
            "extended_capabilities": hasattr(info, "extended_capabilities"),
            "remote_service_capabilities": hasattr(
                info, "remote_service_capabilities"
            ),
            "remote_display": hasattr(info, "remote_display"),
        }
    return meta


def _build_vin_map(coordinator: DataUpdateCoordinator) -> dict[str, str]:
    """Map each real VIN to a stable, non-reversible short token."""
    vin_map: dict[str, str] = {}
    for vd in coordinator.data or []:
        vehicle = vd.get("data")
        vin = getattr(vehicle, "vin", None) if vehicle is not None else None
        if vin:
            vin_map[vin] = "vin:" + hashlib.sha256(vin.encode()).hexdigest()[:8]
    return vin_map


def _tok_str(value: Any, vin_map: dict[str, str]) -> Any:
    """Replace every known VIN *substring* in a string with its stable token.

    Catches the VIN wherever it is embedded — standalone (nickname), as a prefix
    (subscription ids), in URLs (``?vin=``), and in notification text.
    """
    if not isinstance(value, str):
        return value
    for vin, token in vin_map.items():
        if vin in value:
            value = value.replace(vin, token)
    return value


def _deep_redact(obj: Any, vin_map: dict[str, str], _depth: int = 0) -> Any:
    """Recursively blank sensitive keys and tokenise VINs across the export.

    Sensitive keys (GPS, contract id, imei, subscription id, e-mail, ...) become
    ``**REDACTED**``; the VIN is tokenised (keys and string values) rather than
    blanked so a car can still be correlated across sections. ``None`` values are
    left as-is so the shape of the data stays legible.
    """
    if _depth > 12:
        return obj
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            key = _tok_str(k, vin_map)
            if isinstance(k, str) and k.lower() in _REDACT_KEYS and v is not None:
                out[key] = REDACTED
            else:
                out[key] = _deep_redact(v, vin_map, _depth + 1)
        return out
    if isinstance(obj, list):
        return [_deep_redact(v, vin_map, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return _tok_str(obj, vin_map)
    return obj


def _bucket_view(
    bucket: dict[str, Any], vins: set[str] | None = None
) -> dict[str, Any]:
    """JSON-safe, censored view of the per-entry diagnostics bucket.

    ``vins`` (device diagnostics) filters the per-VIN maps to that car. VIN keys
    are tokenised later by ``_deep_redact`` at the top level.
    """
    view: dict[str, Any] = {}
    for key, value in bucket.items():
        if key in _BUCKET_PRESENCE_ONLY and isinstance(value, dict):
            # Holds live Vehicle objects — record presence only, never recurse.
            view[key] = {
                vin: True for vin in value if vins is None or vin in vins
            }
            continue
        if vins is not None and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k in vins}
        view[key] = _jsonify(value)
    return view


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (all vehicles)."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    bucket = hass.data[DOMAIN].get(f"{entry.entry_id}_diag", {})
    vin_map = _build_vin_map(coordinator)

    data = {
        "meta": _pytoyoda_meta(coordinator),
        # entry.title / unique_id embed the account e-mail — omitted; the brand
        # and non-secret options survive via the redacted entry.data below.
        "entry": {
            "version": entry.version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator": _coordinator_block(coordinator),
        "vehicles": [_serialize_vehicle(vd) for vd in (coordinator.data or [])],
        "diagnostics_state": _bucket_view(bucket),
    }
    data = _deep_redact(data, vin_map)
    return async_redact_data(data, TO_REDACT)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics scoped to a single vehicle (device)."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    bucket = hass.data[DOMAIN].get(f"{entry.entry_id}_diag", {})
    vin_map = _build_vin_map(coordinator)

    vins = {ident for domain, ident in device.identifiers if domain == DOMAIN}
    vehicles = [
        _serialize_vehicle(vd)
        for vd in (coordinator.data or [])
        if vd.get("data") is not None and getattr(vd["data"], "vin", None) in vins
    ]

    data = {
        "meta": _pytoyoda_meta(coordinator),
        "device": {"name": device.name, "model": device.model},
        "coordinator": _coordinator_block(coordinator),
        "vehicles": vehicles,
        "diagnostics_state": _bucket_view(bucket, vins=vins),
    }
    data = _deep_redact(data, vin_map)
    return async_redact_data(data, TO_REDACT)
