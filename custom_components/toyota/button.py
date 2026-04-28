"""Per-vehicle refresh-status button.

Wraps the toyota.refresh_vehicle_status service with a one-tap dashboard
entity. Each vehicle gets one button; pressing it triggers the same wake
POST + status poll that the service does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription

from .const import DOMAIN
from .entity import ToyotaBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import VehicleData


REFRESH_BUTTON_DESCRIPTION = ButtonEntityDescription(
    key="refresh_vehicle_status",
    translation_key="refresh_vehicle_status",
    name="Refresh vehicle status",
    icon="mdi:refresh-circle",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota button entities."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]
    async_add_entities(
        ToyotaRefreshStatusButton(
            coordinator=coordinator,
            entry_id=entry.entry_id,
            vehicle_index=index,
            description=REFRESH_BUTTON_DESCRIPTION,
        )
        for index in range(len(coordinator.data))
    )


class ToyotaRefreshStatusButton(ToyotaBaseEntity, ButtonEntity):
    """One-tap wrapper around toyota.refresh_vehicle_status for one VIN."""

    async def async_press(self) -> None:
        """Fire toyota.refresh_vehicle_status for this vehicle's device."""
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get_device(
            identifiers={(DOMAIN, self.vehicle.vin or "")}
        )
        if device is None:
            return
        await self.hass.services.async_call(
            DOMAIN,
            "refresh_vehicle_status",
            {"device_id": [device.id]},
            blocking=False,
        )
