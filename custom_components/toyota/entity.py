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

        self._entry_id = entry_id
        self.entity_description = description
        # The index is only trusted once, here at setup, to learn which VIN this
        # entity belongs to. coordinator.data follows whatever order
        # get_vehicles() returned, which can change between polls, so every
        # later read resolves the vehicle by VIN instead (see _vehicle_data).
        initial = coordinator.data[vehicle_index]
        self.vehicle: Vehicle = initial["data"]
        self.statistics: StatisticsData | None = initial["statistics"]
        self.metric_values: bool = initial["metric_values"]
        self._vin: str | None = self.vehicle.vin

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

    def _vehicle_data(self) -> VehicleData | None:
        """Return this entity's coordinator entry, looked up by VIN.

        The list order is not stable (it mirrors the API response, and vehicles
        can drop out of a cycle), so a positional lookup can hand this entity
        another car's data. Returns None if the VIN is not in the current data.
        """
        return next(
            (vd for vd in self.coordinator.data or [] if vd["data"].vin == self._vin),
            None,
        )

    @property
    def available(self) -> bool:
        """Per-vehicle availability with fault isolation.

        The coordinator must be healthy AND this vehicle's data must be
        either fresh (non-None ``last_successful_fetch``) or a retain-cache
        entry. Stubs (refresh failed for this vehicle specifically, no cache
        to serve) read as unavailable even when the coordinator itself
        succeeded because some sibling vehicle DID refresh fine. This is
        per-vehicle fault isolation: a 429 on one car does not hide the other
        car's data. ToyotaCoordinatorStateSensor (diagnostic sensors)
        overrides this back to True - those must stay visible exactly when
        their vehicle fails, to explain why.
        """
        if not super().available:
            return False
        vd = self._vehicle_data()
        if vd is None:
            return False
        return vd.get("is_cached") or vd.get("last_successful_fetch") is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # If the VIN is missing from this cycle, keep the last known data;
        # `available` already reports the entity unavailable in that case.
        if (vd := self._vehicle_data()) is not None:
            self.vehicle = vd["data"]
            self.statistics = vd["statistics"]
            self.metric_values = vd["metric_values"]
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()
