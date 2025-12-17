"""Toyota Connected Services Climate Control."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
    from pytoyoda.models.endpoints.climate import (
        ClimateControlModel,
        ClimateSettingsResponseModel,
    )


from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.entity import EntityDescription
from pytoyoda.models.endpoints.climate import ClimateControlModel

from .const import DOMAIN
from .entity import ToyotaBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass
class ToyotaClimateEntityDescription(EntityDescription):
    """Describes Toyota climate entity."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota climate entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for index in range(len(coordinator.data)):
        description = ToyotaClimateEntityDescription(
            key="climate",
            name="Climate",
        )
        entities.append(ToyotaClimate(coordinator, entry.entry_id, index, description))

    async_add_entities(entities)


class ToyotaClimate(ToyotaBaseEntity, ClimateEntity):
    """Representation of a Toyota climate control."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_min_temp = 18
    _attr_max_temp = 29
    _attr_target_temperature_step = 1
    _attr_preset_modes = ["none", "front_defrost", "rear_defrost", "both_defrost"]

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        vehicle_index: int,
        description: EntityDescription,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self._climate_settings = None
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 21
        self._attr_preset_mode = "none"

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation mode."""
        if self._climate_settings and self._climate_settings.settings_on:
            return HVACMode.HEAT_COOL
        return HVACMode.OFF

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if self._climate_settings:
            return self._climate_settings.temperature
        return self._attr_target_temperature

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if not self._climate_settings or not self._climate_settings.ac_operations:
            return "none"

        # Find defrost operations
        front_defrost = False
        rear_defrost = False

        for operation in self._climate_settings.ac_operations:
            if operation.category_name == "defrost":
                for param in operation.ac_parameters:
                    if param.name == "frontDefrost" and param.enabled:
                        front_defrost = True
                    elif param.name == "rearDefrost" and param.enabled:
                        rear_defrost = True

        if front_defrost and rear_defrost:
            return "both_defrost"
        if front_defrost:
            return "front_defrost"
        if rear_defrost:
            return "rear_defrost"
        return "none"

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        try:
            climate_settings = await self._get_climate_settings()
            if not climate_settings:
                return

            # Update defrost settings based on preset
            front_enabled = preset_mode in ["front_defrost", "both_defrost"]
            rear_enabled = preset_mode in ["rear_defrost", "both_defrost"]

            # Find defrost operation
            defrost_operation = None
            for operation in climate_settings.ac_operations:
                if operation.category_name == "defrost":
                    defrost_operation = operation
                    break

            if defrost_operation:
                for param in defrost_operation.ac_parameters:
                    if param.name == "frontDefrost":
                        param.enabled = front_enabled
                    elif param.name == "rearDefrost":
                        param.enabled = rear_enabled

            # Send update
            if await self._send_climate_settings(
                climate_settings, f"set defrost preset to {preset_mode}"
            ):
                self._attr_preset_mode = preset_mode
                self.async_write_ha_state()

        except Exception:
            _LOGGER.exception("Error setting preset mode")

    async def async_update(self) -> None:
        """Update climate settings from the car."""
        try:
            response = await self.vehicle._api.get_climate_settings(self.vehicle.vin)
            _LOGGER.debug("async_update called for %s", self.vehicle.alias)
            if response and hasattr(response, "payload") and response.payload:
                self._climate_settings = response.payload
                # Update the HVAC mode based on settings_on
                if self._climate_settings.settings_on:
                    self._attr_hvac_mode = HVACMode.HEAT_COOL
                else:
                    self._attr_hvac_mode = HVACMode.OFF
                _LOGGER.debug(
                    "Updated climate settings for %s: settings_on=%s, temp=%s, hvac_mode=%s",
                    self.vehicle.alias,
                    self._climate_settings.settings_on,
                    self._climate_settings.temperature,
                    self._attr_hvac_mode,
                )
            elif response:
                # Response might be the settings directly, not wrapped in payload
                self._climate_settings = response
                if self._climate_settings.settings_on:
                    self._attr_hvac_mode = HVACMode.HEAT_COOL
                else:
                    self._attr_hvac_mode = HVACMode.OFF
                _LOGGER.debug(
                    "Updated climate settings for %s: settings_on=%s, temp=%s, hvac_mode=%s",
                    self.vehicle.alias,
                    self._climate_settings.settings_on,
                    self._climate_settings.temperature,
                    self._attr_hvac_mode,
                )
        except Exception:
            _LOGGER.exception("Error updating climate settings")

    async def _get_climate_settings(self):
        """Get cached climate settings or fetch from API if not available."""
        if not self._climate_settings:
            _LOGGER.warning(
                "No cached climate settings for %s, fetching from API",
                self.vehicle.alias,
            )
            try:
                response = await self.vehicle._api.get_climate_settings(
                    self.vehicle.vin
                )
                _LOGGER.debug("API response type: %s", type(response))
                _LOGGER.debug("API response: %s", response)

                if not response:
                    _LOGGER.exception(
                        "Failed to get climate settings for - response is None"
                    )
                    return None

                # Check if response has payload attribute
                if hasattr(response, "payload"):
                    _LOGGER.debug("Response has payload attribute")
                    if response.payload:
                        self._climate_settings = response.payload
                        _LOGGER.debug(
                            "Cached climate settings from payload: %s",
                            self._climate_settings,
                        )
                    else:
                        _LOGGER.exception("Response payload is None")
                        return None
                else:
                    # Response might be the settings directly
                    _LOGGER.debug(
                        "Response does not have payload, using response directly"
                    )
                    self._climate_settings = response

            except Exception:
                _LOGGER.exception(
                    "Exception while fetching climate settings",
                )
                return None

        return self._climate_settings

    async def _send_climate_settings(
        self, climate_settings: ClimateSettingsResponseModel, action_description: str
    ) -> bool:
        """Send climate settings to car if climate is on.

        Args:
            climate_settings: The climate settings to send
            action_description: Description of the action for logging

        Returns:
            True if settings were sent (or climate was off), False on error
        """
        try:
            # Only send to API if climate is on
            if climate_settings.settings_on:
                _LOGGER.debug("Sending climate settings to car: %s", action_description)
                status = await self.vehicle._api.update_climate_settings(
                    self.vehicle.vin, climate_settings
                )

                _LOGGER.debug("API response status: %s", status)

                # Check if the update was successful
                if not status or (hasattr(status, "status") and status.status == 0):
                    _LOGGER.exception(
                        "Failed to %s - API returned unsuccessful status",
                        action_description,
                    )
                    return False

                _LOGGER.info(
                    "%s for %s", action_description.capitalize(), self.vehicle.alias
                )
                return True
            _LOGGER.info(
                "%s updated for %s (will apply when turned on)",
                action_description.capitalize(),
                self.vehicle.alias,
            )
            return True
        except Exception:
            _LOGGER.exception("Error during %s", action_description)
            return False

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            await self._turn_off_climate()
        elif hvac_mode == HVACMode.HEAT_COOL:
            await self._turn_on_climate()

    async def async_set_temperature(self, **kwargs: dict) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        try:
            climate_settings = await self._get_climate_settings()
            if not climate_settings:
                return

            # Update temperature in cached settings (don't change settings_on)
            climate_settings.temperature = temperature

            # Send update
            if await self._send_climate_settings(
                climate_settings, f"set temperature to {temperature}°C"
            ):
                self.async_write_ha_state()

        except Exception:
            _LOGGER.exception("Error setting climate temperature: %s")

    async def async_turn_on(self) -> None:
        """Turn on climate control."""
        await self._turn_on_climate()

    async def async_turn_off(self) -> None:
        """Turn off climate control."""
        await self._turn_off_climate()

    async def _turn_on_climate(self) -> None:
        """Turn on the climate control."""
        try:
            _LOGGER.info("Attempting to turn on climate for %s", self.vehicle.alias)
            climate_settings = await self._get_climate_settings()
            if not climate_settings:
                _LOGGER.exception("Failed to get climate settings, cannot turn on")
                return

            # Set to on and ensure temperature is set
            climate_settings.settings_on = True

            # Use current temperature or default
            if not climate_settings.temperature:
                climate_settings.temperature = self._attr_target_temperature

            _LOGGER.info(
                "Updating climate settings for %s: temperature=%s",
                self.vehicle.alias,
                climate_settings.temperature,
            )

            # First, update the settings
            status = await self.vehicle._api.update_climate_settings(
                self.vehicle.vin, climate_settings
            )

            _LOGGER.debug("Update settings response: %s", status)

            # Now send the engine-start command to actually turn on climate
            _LOGGER.info("Sending engine-start command to %s", self.vehicle.alias)

            command_status = await self.vehicle._api.send_climate_control_command(
                self.vehicle.vin, ClimateControlModel(command="engine-start")
            )

            _LOGGER.info("Engine-start command response: %s", command_status)

            # Update local state immediately
            self._attr_hvac_mode = HVACMode.HEAT_COOL
            self.async_write_ha_state()

            _LOGGER.info("Climate control turned on for %s", self.vehicle.alias)
        except Exception:
            _LOGGER.exception("Error turning on climate")

    async def _turn_off_climate(self) -> None:
        """Turn off the climate control."""
        try:
            _LOGGER.info("Attempting to turn off climate for %s", self.vehicle.alias)

            # Send the engine-stop command to turn off climate
            command_status = await self.vehicle._api.send_climate_control_command(
                self.vehicle.vin, ClimateControlModel(command="engine-stop")
            )

            _LOGGER.info("Engine-stop command response: %s", command_status)

            # Also update settings to reflect off state
            climate_settings = await self._get_climate_settings()
            if climate_settings:
                climate_settings.settings_on = False
                await self.vehicle._api.update_climate_settings(
                    self.vehicle.vin, climate_settings
                )

            # Update local state immediately
            self._attr_hvac_mode = HVACMode.OFF
            self.async_write_ha_state()

            _LOGGER.info("Climate control turned off for %s", self.vehicle.alias)
        except Exception:
            _LOGGER.exception("Error turning off climate: %s")
