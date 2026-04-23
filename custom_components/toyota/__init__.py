"""Toyota EU community integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

import asyncio
import asyncio.exceptions as asyncioexceptions
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

import httpcore
import httpx
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from loguru import logger
from pydantic import ValidationError

from .const import (
    CONF_BRAND,
    CONF_METRIC_VALUES,
    CONF_RETAIN_ON_TRANSIENT_FAILURE,
    DEFAULT_RETAIN_ON_TRANSIENT_FAILURE,
    DOMAIN,
    PLATFORMS,
    STARTUP_MESSAGE,
)

_LOGGER = logging.getLogger(__name__)


def loguru_to_hass(message: str) -> None:
    """Forward Loguru logs to standard Python logger used by HACS."""
    level_name = message.record["level"].name.lower()

    if "debug" in level_name:
        _LOGGER.debug(message)
    elif "info" in level_name:
        _LOGGER.info(message)
    elif "warn" in level_name:
        _LOGGER.warning(message)
    elif "error" in level_name:
        _LOGGER.error(message)
    else:
        _LOGGER.critical(message)


logger.remove()
logger.configure(handlers=[{"sink": loguru_to_hass}])

# These imports must be after Loguru configuration to properly intercept logging
from pytoyoda.client import MyT  # noqa: E402
from pytoyoda.exceptions import (  # noqa: E402
    ToyotaApiError,
    ToyotaInternalError,
    ToyotaLoginError,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from pytoyoda.models.summary import Summary
    from pytoyoda.models.vehicle import Vehicle


class StatisticsData(TypedDict):
    """Representing Statistics data."""

    day: Summary | None
    week: Summary | None
    month: Summary | None
    year: Summary | None


class VehicleData(TypedDict):
    """Representing Vehicle data."""

    data: Vehicle
    statistics: StatisticsData | None
    metric_values: bool
    # Observability fields, populated by the coordinator regardless of the
    # CONF_RETAIN_ON_TRANSIENT_FAILURE toggle. Surfaced as timestamp +
    # diagnostic sensors so users can see when their car data was last fresh
    # and what the most recent Toyota-side hiccup was.
    last_successful_fetch: datetime | None
    last_error_time: datetime | None
    last_error_code: str | None
    # True when this poll's data is a cached fallback because the live fetch
    # failed. Used by downstream sensors as a diagnostic.
    is_cached: bool


async def async_setup_entry(  # pylint: disable=too-many-statements # noqa: PLR0915, C901
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Toyota Connected Services from a config entry."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]
    metric_values = entry.data[CONF_METRIC_VALUES]
    brand = entry.data.get(
        CONF_BRAND, "toyota"
    )  # Get brand from config, default to toyota

    # Map brand selection to API brand code
    brand_map = {"toyota": "T", "lexus": "L"}
    brand_code = brand_map.get(brand, "T")

    _LOGGER.info("Setting up %s integration (brand code: %s)", brand, brand_code)

    client = MyT(
        username=email,
        password=password,
        use_metric=metric_values,
        brand=brand_code,
    )

    try:
        await client.login()
    except ToyotaLoginError as ex:
        raise ConfigEntryAuthFailed(ex) from ex
    except (httpx.ConnectTimeout, httpcore.ConnectTimeout) as ex:
        msg = "Unable to connect to Toyota Connected Services"
        raise ConfigEntryNotReady(msg) from ex

    # Per-vehicle retain state. Keyed by VIN. The latest successful
    # VehicleData for each car is kept so that when ONE car's refresh
    # fails (e.g. Toyota 429s partway through the fleet sweep) we can
    # keep that car visible with its last fresh data + a diagnostic
    # last_error timestamp/code, while the other cars get fresh data.
    # Without this, any single transient Toyota failure flips the
    # entire fleet to unavailable.
    #
    # Three additional pieces of state are surfaced as HA sensors:
    # - last_successful_fetch: when this car's data was last fresh
    # - last_error_time: when this car last hit a Toyota-side error
    # - last_error_code: the HTTP status or exception class of that error
    #
    # Gated behind CONF_RETAIN_ON_TRANSIENT_FAILURE so users who rely
    # on "unavailable" state transitions in their automations can opt
    # out. Default off for backward compatibility.
    retain_on_transient: bool = entry.options.get(
        CONF_RETAIN_ON_TRANSIENT_FAILURE, DEFAULT_RETAIN_ON_TRANSIENT_FAILURE
    )
    last_good_per_vin: dict[str, VehicleData] = {}
    last_fetch_time_per_vin: dict[str, datetime] = {}
    last_error_per_vin: dict[str, tuple[datetime, str]] = {}

    def _error_code(exc: BaseException) -> str:
        """Derive a short error-code string for the last_error sensor."""
        msg = str(exc)
        # Toyota 429s embed the status code in the ToyotaApiError message:
        # "Request Failed. 429, {...}." Extract it when present.
        for code in ("429", "500", "502", "503", "504"):
            if f"Request Failed. {code}," in msg:
                return f"HTTP {code}"
        if isinstance(exc, (httpx.ConnectTimeout, httpcore.ConnectTimeout)):
            return "connect timeout"
        if isinstance(exc, (httpx.ReadTimeout, asyncioexceptions.TimeoutError)):
            return "read timeout"
        if isinstance(exc, asyncioexceptions.CancelledError):
            return "cancelled"
        if isinstance(exc, ToyotaApiError):
            return "api error"
        if isinstance(exc, ToyotaLoginError):
            return "login error"
        return type(exc).__name__

    def _build_vehicle_data_from_cache(vin: str) -> VehicleData:
        """Return a copy of the last-good VehicleData for a vin, with
        refreshed error/timestamp fields. The Vehicle object itself is
        the cached one, so all downstream sensors see the last values."""
        cached = last_good_per_vin[vin]
        err = last_error_per_vin.get(vin)
        return VehicleData(
            data=cached["data"],
            statistics=cached["statistics"],
            metric_values=cached["metric_values"],
            last_successful_fetch=last_fetch_time_per_vin.get(vin),
            last_error_time=err[0] if err else None,
            last_error_code=err[1] if err else None,
            is_cached=True,
        )

    async def _refresh_one_vehicle(vehicle: Vehicle) -> VehicleData:
        """Fetch one vehicle's full data. Does NOT catch exceptions; caller
        decides retain-vs-propagate policy based on config toggle."""
        await vehicle.update()
        statistics: StatisticsData | None = None
        if vehicle.vin is not None:
            # Serialised to avoid Toyota burst rate-limit. Firing these four
            # summary calls in an asyncio.gather within the same event-loop
            # tick reliably trips a 429 with {"description": "Unauthorized"}
            # response bodies. See pytoyoda/ha_toyota#282.
            statistics = StatisticsData(
                day=await vehicle.get_current_day_summary(),
                week=await vehicle.get_current_week_summary(),
                month=await vehicle.get_current_month_summary(),
                year=await vehicle.get_current_year_summary(),
            )
        now = dt_util.now()
        if vehicle.vin is not None:
            last_fetch_time_per_vin[vehicle.vin] = now
        err = last_error_per_vin.get(vehicle.vin) if vehicle.vin else None
        return VehicleData(
            data=vehicle,
            statistics=statistics,
            metric_values=metric_values,
            last_successful_fetch=now,
            last_error_time=err[0] if err else None,
            last_error_code=err[1] if err else None,
            is_cached=False,
        )

    async def async_get_vehicle_data() -> list[VehicleData] | None:  # noqa: C901
        """Fetch vehicle data from Toyota API, per-car error handling."""
        # Step 1: get the vehicle list. This is account-level; if it fails
        # we have no per-vehicle recovery path, but we CAN serve stale
        # fleet data if any exists.
        try:
            vehicles = await asyncio.wait_for(client.get_vehicles(), 15)
        except ToyotaLoginError:
            # Credentials invalid - not transient, surface as auth error.
            _LOGGER.exception("Toyota login error")
            return None
        except (
            ToyotaApiError,
            httpx.ConnectTimeout,
            httpcore.ConnectTimeout,
            asyncioexceptions.CancelledError,
            asyncioexceptions.TimeoutError,
            httpx.ReadTimeout,
        ) as ex:
            code = _error_code(ex)
            now = dt_util.now()
            for vin in last_good_per_vin:
                last_error_per_vin[vin] = (now, code)
            if retain_on_transient and last_good_per_vin:
                _LOGGER.warning(
                    "Toyota get_vehicles failed (%s); using cached fleet data", code
                )
                return [_build_vehicle_data_from_cache(vin) for vin in last_good_per_vin]
            raise UpdateFailed(f"Toyota get_vehicles failed: {ex}") from ex
        except ValidationError as ex:
            _LOGGER.exception("Toyota validation error on get_vehicles")
            code = "validation error"
            now = dt_util.now()
            for vin in last_good_per_vin:
                last_error_per_vin[vin] = (now, code)
            if retain_on_transient and last_good_per_vin:
                return [_build_vehicle_data_from_cache(vin) for vin in last_good_per_vin]
            return None

        # Step 2: fetch each vehicle's data independently, so a failure on
        # one does not drop the others. Per-vehicle error recovery honors
        # the retain-on-transient toggle.
        vehicle_informations: list[VehicleData] = []
        for vehicle in vehicles or []:
            if not vehicle or vehicle.vin is None:
                continue
            vin = vehicle.vin
            try:
                vehicle_data = await _refresh_one_vehicle(vehicle)
                last_good_per_vin[vin] = vehicle_data
                vehicle_informations.append(vehicle_data)
            except (
                ToyotaApiError,
                ToyotaInternalError,
                httpx.ConnectTimeout,
                httpcore.ConnectTimeout,
                asyncioexceptions.CancelledError,
                asyncioexceptions.TimeoutError,
                httpx.ReadTimeout,
                ValidationError,
            ) as ex:
                code = _error_code(ex)
                last_error_per_vin[vin] = (dt_util.now(), code)
                _LOGGER.warning(
                    "Toyota refresh failed for vin=...%s (%s)", vin[-6:], code
                )
                if retain_on_transient and vin in last_good_per_vin:
                    vehicle_informations.append(_build_vehicle_data_from_cache(vin))
                elif retain_on_transient:
                    # No cache yet for this vehicle, but we must still emit a
                    # VehicleData so its entities stay registered and just
                    # report None state. Skipping would cause IndexError in
                    # sensor platforms that hold vehicle_index references
                    # from a previous successful refresh.
                    vehicle_informations.append(
                        VehicleData(
                            data=vehicle,
                            statistics=None,
                            metric_values=metric_values,
                            last_successful_fetch=None,
                            last_error_time=dt_util.now(),
                            last_error_code=code,
                            is_cached=False,
                        )
                    )
                # else (retain disabled): skip this vehicle. In this mode the
                # coordinator will raise UpdateFailed if the rest of the
                # fleet also fails, matching upstream behaviour.

        if not vehicle_informations:
            # Whole fleet failed and nothing cached anywhere. Coordinator
            # handling: UpdateFailed flips entities to unavailable.
            raise UpdateFailed("Toyota refresh failed for all vehicles")

        _LOGGER.debug(vehicle_informations)
        return vehicle_informations

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_get_vehicle_data,
        update_interval=timedelta(seconds=360),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
