"""Sensor platform for Toyota integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, List, Literal, Optional, Union

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytoyoda.models.vehicle import Vehicle

from . import StatisticsData, VehicleData
from .const import DOMAIN
from .entity import ToyotaBaseEntity
from .utils import (
    format_statistics_attributes,
    format_vin_sensor_attributes,
    round_number,
)

_LOGGER = logging.getLogger(__name__)


def get_vehicle_capability(
    vehicle, capability_name: str, default: bool = False
) -> bool:
    """Safely retrieve a vehicle capability with a default fallback.

    Args:
        vehicle: The vehicle object
        capability_name: Name of the capability to check
        default: Default return value if capability cannot be retrieved

    Returns:
        bool: Value of the requested capability

    """
    try:
        return getattr(
            getattr(vehicle._vehicle_info, "extended_capabilities", False),
            capability_name,
            default,
        )
    except Exception:  # pylint: disable=W0718
        return default


class ToyotaSensorEntityDescription(SensorEntityDescription, frozen_or_thawed=True):
    """Describes a Toyota sensor entity."""

    value_fn: Callable[[Vehicle], StateType]
    attributes_fn: Callable[[Vehicle], Optional[dict[str, Any]]]


class ToyotaStatisticsSensorEntityDescription(
    SensorEntityDescription, frozen_or_thawed=True
):
    """Describes a Toyota statistics sensor entity."""

    period: Literal["day", "week", "month", "year"]


VIN_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="vin",
    translation_key="vin",
    icon="mdi:car-info",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=SensorDeviceClass.ENUM,
    state_class=None,
    value_fn=lambda vehicle: vehicle.vin,
    attributes_fn=lambda vehicle: format_vin_sensor_attributes(vehicle._vehicle_info),
)
ODOMETER_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="odometer",
    translation_key="odometer",
    icon="mdi:counter",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.TOTAL_INCREASING,
    value_fn=lambda vehicle: None
    if vehicle.dashboard is None
    else round_number(vehicle.dashboard.odometer),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
FUEL_LEVEL_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="fuel_level",
    translation_key="fuel_level",
    icon="mdi:gas-station",
    device_class=None,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: None
    if vehicle.dashboard is None
    else round_number(vehicle.dashboard.fuel_level),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
FUEL_RANGE_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="fuel_range",
    translation_key="fuel_range",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: None
    if vehicle.dashboard is None
    else round_number(vehicle.dashboard.fuel_range),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
BATTERY_LEVEL_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="battery_level",
    translation_key="battery_level",
    icon="mdi:car-electric",
    device_class=SensorDeviceClass.BATTERY,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: None
    if vehicle.dashboard is None
    else round_number(vehicle.dashboard.battery_level),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
BATTERY_RANGE_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="battery_range",
    translation_key="battery_range",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: None
    if vehicle.dashboard is None
    else round_number(vehicle.dashboard.battery_range),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
BATTERY_RANGE_AC_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="battery_range_ac",
    translation_key="battery_range_ac",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: None
    if vehicle.dashboard is None
    else round_number(vehicle.dashboard.battery_range_with_ac),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
TOTAL_RANGE_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="total_range",
    translation_key="total_range",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: None
    if vehicle.dashboard is None
    else round_number(vehicle.dashboard.range),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)

STATISTICS_ENTITY_DESCRIPTIONS_DAILY = ToyotaStatisticsSensorEntityDescription(
    key="current_day_statistics",
    translation_key="current_day_statistics",
    icon="mdi:history",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    period="day",
)

STATISTICS_ENTITY_DESCRIPTIONS_WEEKLY = ToyotaStatisticsSensorEntityDescription(
    key="current_week_statistics",
    translation_key="current_week_statistics",
    icon="mdi:history",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    period="week",
)

STATISTICS_ENTITY_DESCRIPTIONS_MONTHLY = ToyotaStatisticsSensorEntityDescription(
    key="current_month_statistics",
    translation_key="current_month_statistics",
    icon="mdi:history",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    period="month",
)

STATISTICS_ENTITY_DESCRIPTIONS_YEARLY = ToyotaStatisticsSensorEntityDescription(
    key="current_year_statistics",
    translation_key="current_year_statistics",
    icon="mdi:history",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    period="year",
)


def create_sensor_configurations(metric_values: bool) -> List[dict[str, Any]]:
    """Create a list of sensor configurations based on vehicle capabilities.

    Args:
        vehicle: The vehicle object
        metric_values: Whether to use metric units

    Returns:
        List of sensor configurations

    """

    def get_length_unit(metric: bool) -> str:
        return UnitOfLength.KILOMETERS if metric else UnitOfLength.MILES

    return [
        {
            "description": VIN_ENTITY_DESCRIPTION,
            "capability_check": lambda v: True,  # noqa : ARG005
            "native_unit": None,
            "suggested_unit": None,
        },
        {
            "description": ODOMETER_ENTITY_DESCRIPTION,
            "capability_check": lambda v: get_vehicle_capability(
                v, "telemetry_capable"
            ),
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": FUEL_LEVEL_ENTITY_DESCRIPTION,
            "capability_check": lambda v: (
                get_vehicle_capability(v, "fuel_level_available")
                and v.type != "electric"
            ),
            "native_unit": PERCENTAGE,
            "suggested_unit": None,
        },
        {
            "description": FUEL_RANGE_ENTITY_DESCRIPTION,
            "capability_check": lambda v: (
                get_vehicle_capability(v, "fuel_range_available")
                and v.type != "electric"
            ),
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": BATTERY_LEVEL_ENTITY_DESCRIPTION,
            "capability_check": lambda v: get_vehicle_capability(
                v, "econnect_vehicle_status_capable"
            ),
            "native_unit": PERCENTAGE,
            "suggested_unit": None,
        },
        {
            "description": BATTERY_RANGE_ENTITY_DESCRIPTION,
            "capability_check": lambda v: get_vehicle_capability(
                v, "econnect_vehicle_status_capable"
            ),
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": BATTERY_RANGE_AC_ENTITY_DESCRIPTION,
            "capability_check": lambda v: get_vehicle_capability(
                v, "econnect_vehicle_status_capable"
            ),
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": TOTAL_RANGE_ENTITY_DESCRIPTION,
            "capability_check": lambda v: (
                get_vehicle_capability(v, "econnect_vehicle_status_capable")
                and get_vehicle_capability(v, "fuel_range_available")
                and v.type != "electric"
            ),
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": STATISTICS_ENTITY_DESCRIPTIONS_DAILY,
            "capability_check": lambda v: True,  # noqa : ARG005
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": STATISTICS_ENTITY_DESCRIPTIONS_WEEKLY,
            "capability_check": lambda v: True,  # noqa : ARG005
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": STATISTICS_ENTITY_DESCRIPTIONS_MONTHLY,
            "capability_check": lambda v: True,  # noqa : ARG005
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": STATISTICS_ENTITY_DESCRIPTIONS_YEARLY,
            "capability_check": lambda v: True,  # noqa : ARG005
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
    ]


class ToyotaSensor(ToyotaBaseEntity, SensorEntity):
    """Representation of a Toyota sensor."""

    vehicle: Vehicle

    def __init__(  # noqa: PLR0913
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: ToyotaSensorEntityDescription,
        native_unit: Union[UnitOfLength, str],
        suggested_unit: Union[UnitOfLength, str],
    ) -> None:
        """Initialise the ToyotaSensor class."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self.description = description
        self._attr_native_unit_of_measurement = native_unit
        self._attr_suggested_unit_of_measurement = suggested_unit

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.description.value_fn(self.vehicle)

    @property
    def extra_state_attributes(self) -> Optional[dict[str, Any]]:
        """Return the attributes of the sensor."""
        return self.description.attributes_fn(self.vehicle)


class ToyotaStatisticsSensor(ToyotaBaseEntity, SensorEntity):
    """Representation of a Toyota statistics sensor."""

    statistics: StatisticsData

    def __init__(  # noqa: PLR0913
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: ToyotaStatisticsSensorEntityDescription,
        native_unit: Union[UnitOfLength, str],
        suggested_unit: Union[UnitOfLength, str],
    ) -> None:
        """Initialise the ToyotaStatisticsSensor class."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self.period: Literal["day", "week", "month", "year"] = description.period
        self._attr_native_unit_of_measurement = native_unit
        self._attr_suggested_unit_of_measurement = suggested_unit

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        data = self.statistics[self.period]
        return round(data.distance, 1) if data and data.distance else None

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        data = self.statistics[self.period]
        return (
            format_statistics_attributes(data, self.vehicle._vehicle_info)
            if data
            else None
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    sensors: list[Union[ToyotaSensor, ToyotaStatisticsSensor]] = []
    for index, vehicle_data in enumerate(coordinator.data):
        vehicle = vehicle_data["data"]
        metric_values = vehicle_data["metric_values"]

        sensor_configs = create_sensor_configurations(metric_values)

        sensors.extend(
            ToyotaSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=config["description"],
                native_unit=config["native_unit"],
                suggested_unit=config["suggested_unit"],
            )
            for config in sensor_configs
            if not config["description"].key.startswith("current_")
            and config["capability_check"](vehicle)
        )

        # Add statistics sensors
        sensors.extend(
            ToyotaStatisticsSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=config["description"],
                native_unit=config["native_unit"],
                suggested_unit=config["suggested_unit"],
            )
            for config in sensor_configs
            if config["description"].key.startswith("current_")
            and config["capability_check"](vehicle)
        )

    async_add_devices(sensors)
