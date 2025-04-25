"""Binary sensor platform for Toyota integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytoyoda.models.vehicle import Vehicle

from . import VehicleData
from .const import DOMAIN, LAST_UPDATED
from .entity import ToyotaBaseEntity


class ToyotaBinaryEntityDescription(
    BinarySensorEntityDescription, frozen_or_thawed=True
):
    """Describes a Toyota binary entity."""

    value_fn: Callable[[Vehicle], bool | None]
    attributes_fn: Callable[[Vehicle], dict[str, Any] | None]


HOOD_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="hood",
    translation_key="hood",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.DOOR,
    value_fn=lambda vehicle: not getattr(
        getattr(vehicle.lock_status, "hood", None), "closed", None
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

FRONT_DRIVER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="driverseat_lock",
    translation_key="driverseat_lock",
    icon="mdi:car-door-lock",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.LOCK,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "driver_seat", None),
        "locked",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

FRONT_DRIVER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="driverseat_door",
    translation_key="driverseat_door",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.DOOR,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "driver_seat", None),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

FRONT_DRIVER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="driverseat_window",
    translation_key="driverseat_window",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.WINDOW,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "windows", None), "driver_seat", None),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

FRONT_PASSENGER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="passengerseat_lock",
    translation_key="passengerseat_lock",
    icon="mdi:car-door-lock",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.LOCK,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "passenger_seat", None),
        "locked",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

FRONT_PASSENGER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="passengerseat_door",
    translation_key="passengerseat_door",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.DOOR,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "passenger_seat", None),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

FRONT_PASSENGER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="passengerseat_window",
    translation_key="passengerseat_window",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.WINDOW,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "windows", None), "passenger_seat", None),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

REAR_DRIVER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="leftrearseat_lock",
    translation_key="leftrearseat_lock",
    icon="mdi:car-door-lock",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.LOCK,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "driver_rear_seat", None),
        "locked",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

REAR_DRIVER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="leftrearseat_door",
    translation_key="leftrearseat_door",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.DOOR,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "driver_rear_seat", None),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

REAR_DRIVER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="leftrearseat_window",
    translation_key="leftrearseat_window",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.WINDOW,
    value_fn=lambda vehicle: not getattr(
        getattr(
            getattr(vehicle.lock_status, "windows", None), "driver_rear_seat", None
        ),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

REAR_PASSENGER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="rightrearseat_lock",
    translation_key="rightrearseat_lock",
    icon="mdi:car-door-lock",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.LOCK,
    value_fn=lambda vehicle: not getattr(
        getattr(
            getattr(vehicle.lock_status, "doors", None), "passenger_rear_seat", None
        ),
        "locked",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

REAR_PASSENGER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="rightrearseat_door",
    translation_key="rightrearseat_door",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.DOOR,
    value_fn=lambda vehicle: not getattr(
        getattr(
            getattr(vehicle.lock_status, "doors", None), "passenger_rear_seat", None
        ),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

REAR_PASSENGER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="rightrearseat_window",
    translation_key="rightrearseat_window",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.WINDOW,
    value_fn=lambda vehicle: not getattr(
        getattr(
            getattr(vehicle.lock_status, "windows", None), "passenger_rear_seat", None
        ),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="trunk_lock",
    translation_key="trunk_lock",
    icon="mdi:car-door-lock",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.LOCK,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "trunk", None),
        "locked",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)

TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION = ToyotaBinaryEntityDescription(
    key="trunk_door",
    translation_key="trunk_door",
    icon="mdi:car-door",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=BinarySensorDeviceClass.WINDOW,
    value_fn=lambda vehicle: not getattr(
        getattr(getattr(vehicle.lock_status, "doors", None), "trunk", None),
        "closed",
        None,
    ),
    attributes_fn=lambda vehicle: {
        LAST_UPDATED: getattr(vehicle.lock_status, "last_updated", None),
    },
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    binary_sensors: list[ToyotaBinarySensor] = []
    for index, _ in enumerate(coordinator.data):
        vehicle = coordinator.data[index]["data"]
        capabilities_descriptions = [
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "bonnet_status",
                    False,
                ),
                HOOD_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "front_driver_door_lock_status",
                    False,
                ),
                FRONT_DRIVER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "front_driver_door_open_status",
                    False,
                ),
                FRONT_DRIVER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "front_driver_door_window_status",
                    False,
                ),
                FRONT_DRIVER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "front_passenger_door_lock_status",
                    False,
                ),
                FRONT_PASSENGER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "front_passenger_door_open_status",
                    False,
                ),
                FRONT_PASSENGER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "front_passenger_door_window_status",
                    False,
                ),
                FRONT_PASSENGER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "rear_driver_door_lock_status",
                    False,
                ),
                REAR_DRIVER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "rear_driver_door_open_status",
                    False,
                ),
                REAR_DRIVER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "rear_driver_door_window_status",
                    False,
                ),
                REAR_DRIVER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "rear_passenger_door_lock_status",
                    False,
                ),
                REAR_PASSENGER_DOOR_LOCK_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "rear_passenger_door_open_status",
                    False,
                ),
                REAR_PASSENGER_DOOR_OPEN_STATUS_ENTITY_DESCRIPTION,
            ),
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "rear_passenger_door_window_status",
                    False,
                ),
                REAR_PASSENGER_DOOR_WINDOW_STATUS_ENTITY_DESCRIPTION,
            ),
            # TODO: Find correct matching capabilities in _vehicle_info
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "bonnet_status",
                    False,
                ),
                TRUNK_DOOR_LOCK_ENTITY_DESCRIPTION,
            ),
            # TODO: Find correct matching capabilities in _vehicle_info
            (
                getattr(
                    getattr(vehicle._vehicle_info, "extended_capabilities", False),
                    "bonnet_status",
                    False,
                ),
                TRUNK_DOOR_OPEN_ENTITY_DESCRIPTION,
            ),
        ]

        binary_sensors.extend(
            ToyotaBinarySensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                vehicle_index=index,
                description=description,
            )
            for capability, description in capabilities_descriptions
            if capability
        )
    async_add_devices(binary_sensors, True)


class ToyotaBinarySensor(ToyotaBaseEntity, BinarySensorEntity):
    """Representation of a Toyota binary sensor."""

    @property
    def is_on(self) -> bool | None:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.vehicle)  # type: ignore[reportAttributeAccessIssue, attr-defined]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the attributes of the sensor."""
        return self.entity_description.attributes_fn(self.vehicle)  # type: ignore[reportAttributeAccessIssue, attr-defined]
