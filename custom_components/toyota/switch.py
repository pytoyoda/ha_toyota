"""Toyota Connected Services steering-wheel heater control.

Already surfaced read-only via the climate entity's extra_state_attributes;
this entity makes it controllable. Toyota's remote API has no settings-only
write, so toggling it sends a full climate-control ``start`` (see
``async_apply_climate_settings`` in ``climate.py``) - this also (re)starts
remote climate control, mirroring the MyToyota app's own behavior for this
control.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .climate import async_apply_climate_settings
from .const import DOMAIN
from .entity import ToyotaBaseEntity
from .utils import vehicle_has_climate_capability

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import VehicleData

_LOGGER = logging.getLogger(__name__)

STEERING_HEATER_DESCRIPTION = SwitchEntityDescription(
    key="steering_heater",
    translation_key="steering_heater",
    name="Steering wheel heater",
    icon="mdi:steering",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota steering-heater switch entities."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    entities: list[ToyotaSteeringHeaterSwitch] = []
    for index, vehicle_data in enumerate(coordinator.data):
        vehicle = vehicle_data["data"]
        if not vehicle_has_climate_capability(vehicle):
            continue
        heating = getattr(
            getattr(vehicle, "climate_settings", None), "heating_options", None
        )
        if getattr(heating, "steering_heater", None) is None:
            continue
        entities.append(
            ToyotaSteeringHeaterSwitch(
                coordinator,
                entry.entry_id,
                index,
                STEERING_HEATER_DESCRIPTION,
            )
        )
    async_add_entities(entities)


class ToyotaSteeringHeaterSwitch(ToyotaBaseEntity, SwitchEntity):
    """Steering-wheel heater on/off control."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialize the steering-heater switch entity."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        # Optimistic write-through: see ToyotaSeatHeaterSelect for rationale.
        self._pending_state: bool | None = None

    def _read_is_on(self) -> bool | None:
        """Read the steering heater's current on/off state from climate_settings."""
        heating = getattr(
            getattr(self.vehicle, "climate_settings", None), "heating_options", None
        )
        # HeatingOptions.steering_heater already reads back as bool | None.
        return getattr(heating, "steering_heater", None)

    @property
    def is_on(self) -> bool | None:
        """Return True if the steering wheel heater is on."""
        if self._pending_state is not None:
            return self._pending_state
        return self._read_is_on()

    async def async_turn_on(self, **kwargs: object) -> None:  # noqa: ARG002
        """Turn the steering wheel heater on."""
        await self._async_set_state(state=True)

    async def async_turn_off(self, **kwargs: object) -> None:  # noqa: ARG002
        """Turn the steering wheel heater off."""
        await self._async_set_state(state=False)

    async def _async_set_state(self, *, state: bool) -> None:
        """Send the desired steering-heater state (sends a climate-control start)."""
        previous = self._pending_state
        self._pending_state = state
        self.async_write_ha_state()
        try:
            await async_apply_climate_settings(
                self.vehicle, steering_heater="on" if state else "off"
            )
        except Exception as err:
            self._pending_state = previous
            self.async_write_ha_state()
            if isinstance(err, HomeAssistantError):
                raise
            msg = f"Failed to set steering wheel heater: {err}"
            raise HomeAssistantError(msg) from err

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()
        if self._pending_state is not None and (
            self._read_is_on() == self._pending_state
        ):
            self._pending_state = None
