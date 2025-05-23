"""Custom coordinator entity base classes for Toyota Connected Services integration."""

# pylint: disable=W0212, W0511

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import CONF_BRAND_MAPPING, DOMAIN

if TYPE_CHECKING:
    from pytoyoda.models.vehicle import Vehicle

    from . import StatisticsData, VehicleData


class ToyotaBaseEntity(CoordinatorEntity):
    """Defines a base Toyota entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: EntityDescription,
    ) -> None:
        """Initialize the Toyota entity."""
        super().__init__(coordinator)  # type: ignore[reportArgumentType, arg-type]

        self.index = vehicle_index
        self.entity_description = description
        self.vehicle: Vehicle = coordinator.data[self.index]["data"]
        self.statistics: StatisticsData | None = coordinator.data[self.index][
            "statistics"
        ]
        self.metric_values: bool = coordinator.data[self.index]["metric_values"]

        self._attr_unique_id = (
            f"{entry_id}_{self.vehicle.vin}/{self.entity_description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.vehicle.vin or "Unknown")},
            name=self.vehicle.alias,
            model=self.vehicle._vehicle_info.car_model_name,  # noqa : SLF001
            manufacturer=CONF_BRAND_MAPPING.get(self.vehicle._vehicle_info.brand)  # noqa : SLF001
            if self.vehicle._vehicle_info.brand  # noqa : SLF001
            else "Unknown",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.vehicle = self.coordinator.data[self.index]["data"]
        self.statistics = self.coordinator.data[self.index]["statistics"]
        self.metric_values = self.coordinator.data[self.index]["metric_values"]
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()
