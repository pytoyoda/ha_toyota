"""Toyota integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

import asyncio
import asyncio.exceptions as asyncioexceptions
import logging
from datetime import timedelta
from functools import partial
from typing import Optional, TypedDict

import httpcore
import httpx
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from loguru import logger
from pydantic import ValidationError

from .const import CONF_METRIC_VALUES, DOMAIN, PLATFORMS, STARTUP_MESSAGE

_LOGGER = logging.getLogger(__name__)


def loguru_to_hass(message: str) -> None:
    """Forwards Loguru logs to standard python logger used by HACS."""
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

# These imports must be after loguru configuration to properly intercept logging
from pytoyoda.client import MyT  # noqa: E402
from pytoyoda.exceptions import (  # noqa: E402
    ToyotaApiError,
    ToyotaInternalError,
    ToyotaLoginError,
)
from pytoyoda.models.summary import Summary  # noqa: E402
from pytoyoda.models.vehicle import Vehicle  # noqa: E402


class StatisticsData(TypedDict):
    """Representing Statistics data."""

    day: Optional[Summary]
    week: Optional[Summary]
    month: Optional[Summary]
    year: Optional[Summary]


class VehicleData(TypedDict):
    """Representing Vehicle data."""

    data: Vehicle
    statistics: Optional[StatisticsData]
    metric_values: bool


async def async_setup_entry(  # pylint: disable=too-many-statements # noqa: PLR0915
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Toyota Connected Services from a config entry."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]
    metric_values = entry.data[CONF_METRIC_VALUES]

    client = await hass.async_add_executor_job(
        partial(MyT, username=email, password=password, use_metric=metric_values)
    )

    try:

        def _sync_login():
            loop = asyncio.new_event_loop()
            result = None
            try:
                result = loop.run_until_complete(client.login())
            finally:
                loop.close()
            return result

        await hass.async_add_executor_job(_sync_login)
    except ToyotaLoginError as ex:
        raise ConfigEntryAuthFailed(ex) from ex
    except (httpx.ConnectTimeout, httpcore.ConnectTimeout) as ex:
        raise ConfigEntryNotReady(
            "Unable to connect to Toyota Connected Services"
        ) from ex

    async def async_get_vehicle_data() -> Optional[list[VehicleData]]:
        """Fetch vehicle data from Toyota API."""
        try:
            vehicles = await asyncio.wait_for(client.get_vehicles(), 15)
            vehicle_informations: list[VehicleData] = []
            if vehicles:
                for vehicle in vehicles:
                    if vehicle:
                        await vehicle.update()
                        vehicle_data = VehicleData(
                            data=vehicle, statistics=None, metric_values=metric_values
                        )

                        if vehicle.vin is not None:
                            # Use parallel request to get car statistics.
                            driving_statistics = await asyncio.gather(
                                vehicle.get_current_day_summary(),
                                vehicle.get_current_week_summary(),
                                vehicle.get_current_month_summary(),
                                vehicle.get_current_year_summary(),
                            )

                            vehicle_data["statistics"] = StatisticsData(
                                day=driving_statistics[0],
                                week=driving_statistics[1],
                                month=driving_statistics[2],
                                year=driving_statistics[3],
                            )

                        vehicle_informations.append(vehicle_data)

                _LOGGER.debug(vehicle_informations)
                return vehicle_informations

        except ToyotaLoginError as ex:
            _LOGGER.error(ex)
        except ToyotaInternalError as ex:
            _LOGGER.debug(ex)
        except ToyotaApiError as ex:
            raise UpdateFailed(ex) from ex
        except (httpx.ConnectTimeout, httpcore.ConnectTimeout) as ex:
            raise UpdateFailed("Unable to connect to Toyota Connected Services") from ex
        except ValidationError as ex:
            _LOGGER.error(ex)
        except (
            asyncioexceptions.CancelledError,
            asyncioexceptions.TimeoutError,
            httpx.ReadTimeout,
        ) as ex:
            raise UpdateFailed(
                "Update canceled! \n"
                "Toyota's API was too slow to respond. Will try again later."
            ) from ex
        return None

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
