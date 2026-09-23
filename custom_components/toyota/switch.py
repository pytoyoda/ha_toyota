"""Toyota Connected Services steering-wheel heater and hazard-light switches.

Steering-wheel heater: already surfaced read-only via the climate entity's
extra_state_attributes; this entity makes it controllable. Toyota's remote
API has no settings-only write, so toggling it sends a full climate-control
``start`` (see ``async_apply_climate_settings`` in ``climate.py``) - this
also (re)starts remote climate control, mirroring the MyToyota app's own
behavior for this control.

Hazard lights: a plain remote command (``CommandType.HAZARD_ON`` /
``HAZARD_OFF``, see #428). Toyota's API has no hazard-light status
readback, so this switch is purely optimistic/write-only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from pytoyoda.models.endpoints.command import CommandType

from .climate import async_apply_climate_settings
from .const import DOMAIN
from .entity import ToyotaBaseEntity
from .utils import command_failure_reason, vehicle_has_climate_capability

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
    from pytoyoda.models.vehicle import Vehicle

    from . import VehicleData

_LOGGER = logging.getLogger(__name__)

STEERING_HEATER_DESCRIPTION = SwitchEntityDescription(
    key="steering_heater",
    translation_key="steering_heater",
    name="Steering wheel heater",
    icon="mdi:steering",
)

HAZARD_LIGHTS_DESCRIPTION = SwitchEntityDescription(
    key="hazard_lights",
    translation_key="hazard_lights",
    name="Hazard lights",
    icon="mdi:hazard-lights",
)


def _hazard_capable(vehicle: Vehicle) -> bool:
    """Return whether *vehicle* explicitly advertises remote hazard-light control."""
    info = getattr(vehicle, "_vehicle_info", None)
    return any(
        getattr(getattr(info, block, None), "hazard_capable", False) is True
        for block in ("extended_capabilities", "remote_service_capabilities")
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

    entities: list[SwitchEntity] = []
    for index, vehicle_data in enumerate(coordinator.data):
        vehicle = vehicle_data["data"]
        if vehicle_has_climate_capability(vehicle):
            heating = getattr(
                getattr(vehicle, "climate_settings", None), "heating_options", None
            )
            if getattr(heating, "steering_heater", None) is not None:
                entities.append(
                    ToyotaSteeringHeaterSwitch(
                        coordinator,
                        entry.entry_id,
                        index,
                        STEERING_HEATER_DESCRIPTION,
                    )
                )
        if _hazard_capable(vehicle):
            entities.append(
                ToyotaHazardLightsSwitch(
                    coordinator,
                    entry.entry_id,
                    index,
                    HAZARD_LIGHTS_DESCRIPTION,
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


class ToyotaHazardLightsSwitch(ToyotaBaseEntity, SwitchEntity):
    """Remote hazard-light (flasher) on/off control.

    Toyota's command API has no hazard-light status readback, unlike doors
    (see ``lock.py``), so this entity is purely optimistic: ``is_on``
    reflects only the last command sent, never confirmed vehicle telemetry,
    and resets to "off" whenever the entity is (re)created, e.g. on restart.
    """

    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialize with the hazard lights assumed off."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self._is_on = False

    @property
    def is_on(self) -> bool:
        """Return the last commanded hazard-light state."""
        return self._is_on

    async def async_turn_on(self, **kwargs: object) -> None:  # noqa: ARG002
        """Turn the hazard lights on."""
        await self._async_set_state(state=True)

    async def async_turn_off(self, **kwargs: object) -> None:  # noqa: ARG002
        """Turn the hazard lights off."""
        await self._async_set_state(state=False)

    async def _async_set_state(self, *, state: bool) -> None:
        """Send the hazard on/off remote command."""
        command = CommandType.HAZARD_ON if state else CommandType.HAZARD_OFF
        try:
            response = await self.vehicle.post_command(command)
        except Exception as err:
            msg = "Toyota could not send the hazard-light command"
            raise HomeAssistantError(msg) from err
        if reason := command_failure_reason(response):
            raise HomeAssistantError(reason)
        self._is_on = state
        self.async_write_ha_state()
