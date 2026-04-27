"""Sensor platform for Toyota integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import ToyotaBaseEntity
from .utils import (
    charging_status_key,
    format_statistics_attributes,
    format_vin_sensor_attributes,
    round_number,
    td_to_hoursminutes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
    from pytoyoda.models.vehicle import Vehicle

    from . import StatisticsData, VehicleData

_LOGGER = logging.getLogger(__name__)


def get_vehicle_capability(
    vehicle: Vehicle,
    capability_name: str,
    default: bool = False,  # noqa: FBT001, FBT002
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
            getattr(vehicle._vehicle_info, "extended_capabilities", False),  # noqa : SLF001
            capability_name,
            default,
        )
    except Exception:  # pylint: disable=W0718 # noqa : BLE001
        return default


class ToyotaSensorEntityDescription(SensorEntityDescription, frozen_or_thawed=True):
    """Describes a Toyota sensor entity."""

    value_fn: Callable[[Vehicle], StateType]
    attributes_fn: Callable[[Vehicle], dict[str, Any] | None]


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
    attributes_fn=lambda vehicle: format_vin_sensor_attributes(vehicle._vehicle_info),  # noqa : SLF001
)
ODOMETER_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="odometer",
    translation_key="odometer",
    icon="mdi:counter",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.TOTAL_INCREASING,
    value_fn=lambda vehicle: (
        None if vehicle.dashboard is None else round_number(vehicle.dashboard.odometer)
    ),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
FUEL_LEVEL_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="fuel_level",
    translation_key="fuel_level",
    icon="mdi:gas-station",
    device_class=None,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: (
        None
        if vehicle.dashboard is None
        else round_number(vehicle.dashboard.fuel_level)
    ),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
FUEL_RANGE_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="fuel_range",
    translation_key="fuel_range",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: (
        None
        if vehicle.dashboard is None
        else round_number(vehicle.dashboard.fuel_range)
    ),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
BATTERY_LEVEL_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="battery_level",
    translation_key="battery_level",
    icon="mdi:car-electric",
    device_class=SensorDeviceClass.BATTERY,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: (
        None
        if vehicle.dashboard is None
        else round_number(vehicle.dashboard.battery_level)
    ),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
BATTERY_RANGE_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="battery_range",
    translation_key="battery_range",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: (
        None
        if vehicle.dashboard is None
        else round_number(vehicle.dashboard.battery_range)
    ),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
BATTERY_RANGE_AC_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="battery_range_ac",
    translation_key="battery_range_ac",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: (
        None
        if vehicle.dashboard is None
        else round_number(vehicle.dashboard.battery_range_with_ac)
    ),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
TOTAL_RANGE_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="total_range",
    translation_key="total_range",
    icon="mdi:map-marker-distance",
    device_class=SensorDeviceClass.DISTANCE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda vehicle: (
        None if vehicle.dashboard is None else round_number(vehicle.dashboard.range)
    ),
    suggested_display_precision=0,
    attributes_fn=lambda vehicle: None,  # noqa : ARG005
)
CHARGING_STATUS_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="charging_status",
    translation_key="charging_status",
    icon="mdi:ev-station",
    device_class=SensorDeviceClass.ENUM,
    options=["charge_complete", "charging", "none", "plugged"],
    value_fn=lambda vehicle: (
        None
        if vehicle.dashboard is None
        else charging_status_key(vehicle.dashboard.charging_status)
    ),
    attributes_fn=lambda vehicle: (
        None
        if vehicle.dashboard is None
        else {
            **(
                {
                    "remaining_minutes": int(
                        vehicle.dashboard.remaining_charge_time.total_seconds() // 60
                    )
                }
                if vehicle.dashboard.remaining_charge_time is not None
                else {}
            ),
            "has_charging_schedule": vehicle.electric_status.has_active_charging_schedule  # noqa : E501
            if hasattr(vehicle.electric_status, "has_active_charging_schedule")
            and vehicle.electric_status.has_active_charging_schedule
            else None,
            **(
                {
                    "scheduled_charging_start": (
                        vehicle.electric_status.active_scheduled_charging.start
                    ),
                    "scheduled_charging_end": (
                        vehicle.electric_status.active_scheduled_charging.end
                    ),
                    "scheduled_charging_duration": None
                    if vehicle.electric_status.active_scheduled_charging.duration
                    is None
                    else td_to_hoursminutes(
                        vehicle.electric_status.active_scheduled_charging.duration
                    ),
                }
                if hasattr(vehicle.electric_status, "has_active_charging_schedule")
                and vehicle.electric_status.has_active_charging_schedule
                else {}
            ),
        }
    ),
)
REMAINING_CHARGE_TIME_ENTITY_DESCRIPTION = ToyotaSensorEntityDescription(
    key="remaining_charge_time",
    translation_key="remaining_charge_time",
    icon="mdi:battery-clock",
    device_class=SensorDeviceClass.DURATION,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    value_fn=lambda vehicle: (
        None
        if (
            vehicle.dashboard is None or vehicle.dashboard.remaining_charge_time is None
        )
        else (vehicle.dashboard.remaining_charge_time.total_seconds() // 60)
    ),
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


def create_sensor_configurations(metric_values: bool) -> list[dict[str, Any]]:  # noqa : FBT001
    """Create a list of sensor configurations based on vehicle capabilities.

    Args:
        vehicle: The vehicle object
        metric_values: Whether to use metric units

    Returns:
        List of sensor configurations

    """

    def get_length_unit(metric: bool) -> str:  # noqa : FBT001
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
            "capability_check": lambda v: (
                get_vehicle_capability(v, "econnect_vehicle_status_capable")
                or v.type == "electric"
            ),
            "native_unit": PERCENTAGE,
            "suggested_unit": None,
        },
        {
            "description": BATTERY_RANGE_ENTITY_DESCRIPTION,
            "capability_check": lambda v: (
                get_vehicle_capability(v, "econnect_vehicle_status_capable")
                or v.type == "electric"
            ),
            "native_unit": get_length_unit(metric_values),
            "suggested_unit": get_length_unit(metric_values),
        },
        {
            "description": BATTERY_RANGE_AC_ENTITY_DESCRIPTION,
            "capability_check": lambda v: (
                get_vehicle_capability(v, "econnect_vehicle_status_capable")
                or v.type == "electric"
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
            "description": CHARGING_STATUS_ENTITY_DESCRIPTION,
            "capability_check": lambda v: (
                get_vehicle_capability(v, "econnect_vehicle_status_capable")
                or v.type == "electric"
            ),
            "native_unit": None,
            "suggested_unit": None,
        },
        {
            "description": REMAINING_CHARGE_TIME_ENTITY_DESCRIPTION,
            "capability_check": lambda v: (
                get_vehicle_capability(v, "econnect_vehicle_status_capable")
                or v.type == "electric"
            ),
            "native_unit": "min",
            "suggested_unit": "min",
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
        native_unit: UnitOfLength | str,
        suggested_unit: UnitOfLength | str,
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
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the attributes of the sensor."""
        return self.description.attributes_fn(self.vehicle)


LAST_SUCCESSFUL_FETCH_ENTITY_DESCRIPTION = SensorEntityDescription(
    key="last_successful_fetch",
    translation_key="last_successful_fetch",
    name="Last successful fetch",
    icon="mdi:clock-check-outline",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)
LAST_ERROR_TIME_ENTITY_DESCRIPTION = SensorEntityDescription(
    key="last_error_time",
    translation_key="last_error_time",
    name="Last error",
    icon="mdi:clock-alert-outline",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)
LAST_ERROR_CODE_ENTITY_DESCRIPTION = SensorEntityDescription(
    key="last_error_code",
    translation_key="last_error_code",
    name="Last error code",
    icon="mdi:alert-outline",
    entity_category=EntityCategory.DIAGNOSTIC,
)
STATUS_LAST_REPORTED_ENTITY_DESCRIPTION = SensorEntityDescription(
    key="status_last_reported",
    translation_key="status_last_reported",
    name="Status last reported by car",
    icon="mdi:car-clock",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)
STATUS_REFRESH_STATE_ENTITY_DESCRIPTION = SensorEntityDescription(
    key="status_refresh_state",
    translation_key="status_refresh_state",
    name="Status refresh state",
    icon="mdi:refresh-auto",
    device_class=SensorDeviceClass.ENUM,
    options=[
        "active",
        "soft_disabled_unreachable",
        "hard_disabled_auto",
        "hard_disabled_user",
    ],
    entity_category=EntityCategory.DIAGNOSTIC,
)


class ToyotaCoordinatorStateSensor(ToyotaBaseEntity, SensorEntity):
    """Sensor backed by per-VIN diagnostic dicts on the coordinator.

    Used for observability sensors (last_successful_fetch, last_error_time,
    last_error_code, status_last_reported, status_refresh_state) that
    describe the fetch itself or the strategy's state, not the vehicle.

    Two overrides are in play:

    1. `available` is forced True. These sensors exist to explain WHY the
       data sensors went unavailable, so they themselves must never go
       unavailable. HA's DataUpdateCoordinator drives CoordinatorEntity's
       availability off `last_update_success`, which flips False on
       UpdateFailed; we explicitly unbind from that signal.

    2. `native_value` reads from the per-VIN dicts attached to the
       coordinator (`_diag_last_fetch_per_vin`, `_diag_last_error_per_vin`)
       instead of `coordinator.data`. With retain_on_transient=False and a
       full-fleet 429, `async_get_vehicle_data` raises UpdateFailed before
       appending any VehicleData, so coordinator.data stays frozen at the
       last SUCCESSFUL refresh (where the error fields were None). Reading
       from the per-VIN dicts instead means error info appears as soon as
       it's known, regardless of retain toggle or UpdateFailed.
    """

    _DIAG_KEY_MAP: ClassVar[dict[str, tuple[str, int | None]]] = {
        "last_successful_fetch": ("_diag_last_fetch_per_vin", None),
        "last_error_time": ("_diag_last_error_per_vin", 0),
        "last_error_code": ("_diag_last_error_per_vin", 1),
        "status_last_reported": ("_diag_status_occurrence_per_vin", None),
        "status_refresh_state": ("_diag_status_refresh_state_per_vin", None),
    }

    @property
    def available(self) -> bool:
        """Diagnostic sensors are always considered available."""
        return True

    @property
    def native_value(self) -> StateType:
        """Return the value from the coordinator's per-VIN diagnostic dicts."""
        vin = getattr(self.vehicle, "vin", None)
        if not vin:
            return None
        key = self.entity_description.key
        attr_name, tuple_idx = self._DIAG_KEY_MAP.get(key, (None, None))
        if attr_name is None:
            return None
        per_vin = getattr(self.coordinator, attr_name, None)
        if per_vin is None:
            return None
        value = per_vin.get(vin)
        if value is None:
            return None
        return value if tuple_idx is None else value[tuple_idx]


class ToyotaStatisticsSensor(ToyotaBaseEntity, SensorEntity):
    """Representation of a Toyota statistics sensor."""

    statistics: StatisticsData

    def __init__(  # noqa: PLR0913
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: ToyotaStatisticsSensorEntityDescription,
        native_unit: UnitOfLength | str,
        suggested_unit: UnitOfLength | str,
    ) -> None:
        """Initialise the ToyotaStatisticsSensor class."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self.period: Literal["day", "week", "month", "year"] = description.period
        self._attr_native_unit_of_measurement = native_unit
        self._attr_suggested_unit_of_measurement = suggested_unit

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.statistics is None:
            return None
        data = self.statistics[self.period]
        return round(data.distance, 1) if data and data.distance else None

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return the state attributes."""
        if self.statistics is None:
            return None
        data = self.statistics[self.period]
        return (
            format_statistics_attributes(data, self.vehicle._vehicle_info)  # noqa : SLF001
            if data
            else None
        )


RECENT_TRIPS_ENTITY_DESCRIPTION = SensorEntityDescription(
    key="recent_trips",
    translation_key="recent_trips",
    icon="mdi:road-variant",
)


class ToyotaRecentTripsSensor(ToyotaBaseEntity, SensorEntity):
    """Sensor exposing the recent-trips rolling cache for one VIN.

    State: count of trips currently in the cache (0 when disabled or not
    yet seeded). Attributes:

    - ``trips``: list of trip dicts in journey-viewer-card data contract shape
    - ``last_trip_label``: human-readable label for the most recent trip
      (convenience for templates that don't want to parse the array)
    - ``source``: ``"toyota"`` (cosmetic, for cards that group by source)

    Sensor reads from the manager's cache on every coordinator update.
    Cache mutations happen during _refresh_one_vehicle (in the coordinator's
    update path), so the next coordinator-driven sensor refresh sees fresh
    state automatically.
    """

    @property
    def available(self) -> bool:
        """Available once the manager exists; cache being empty is a valid state."""
        if not super().available:
            return False
        mgr = self.hass.data.get(DOMAIN, {}).get(f"{self._entry_id}_trips_manager")
        return mgr is not None

    @property
    def native_value(self) -> StateType:
        """Number of trips currently cached for this VIN."""
        mgr = self.hass.data.get(DOMAIN, {}).get(f"{self._entry_id}_trips_manager")
        if mgr is None:
            return None
        vin = getattr(self.vehicle, "vin", None)
        if not vin:
            return None
        return len(mgr.cache.get(vin))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the cached trips list and a convenience label."""
        mgr = self.hass.data.get(DOMAIN, {}).get(f"{self._entry_id}_trips_manager")
        if mgr is None:
            return None
        vin = getattr(self.vehicle, "vin", None)
        if not vin:
            return None
        trips = mgr.cache.get(vin)
        last_trip_label: str | None = None
        if trips:
            first = trips[0]
            stats = first.get("stats", {})
            ts = first.get("start_ts") or "?"
            distance_m = stats.get("distance_m")
            if isinstance(distance_m, (int, float)):
                distance_str = f"{distance_m / 1000:.2f} km"
            else:
                distance_str = "?"
            last_trip_label = f"{ts} ({distance_str})"
        return {
            "trips": trips,
            "last_trip_label": last_trip_label,
            "source": "toyota",
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    sensors: list[ToyotaSensor | ToyotaStatisticsSensor] = []
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

        # Add coordinator-state observability sensors (always on, not
        # gated by CONF_RETAIN_ON_TRANSIENT_FAILURE; they are read-only).
        sensors.extend(
            ToyotaCoordinatorStateSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=desc,
            )
            for desc in (
                LAST_SUCCESSFUL_FETCH_ENTITY_DESCRIPTION,
                LAST_ERROR_TIME_ENTITY_DESCRIPTION,
                LAST_ERROR_CODE_ENTITY_DESCRIPTION,
                STATUS_LAST_REPORTED_ENTITY_DESCRIPTION,
                STATUS_REFRESH_STATE_ENTITY_DESCRIPTION,
            )
        )

        # Recent-trips sensor: always created (so users discover the entity);
        # state reads "0 trips" when CONF_MAX_RECENT_TRIPS=0 and no service
        # call has been used to seed it.
        sensors.append(
            ToyotaRecentTripsSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=RECENT_TRIPS_ENTITY_DESCRIPTION,
            )
        )

    async_add_devices(sensors)
