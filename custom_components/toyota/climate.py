"""Toyota Connected Services Climate Control."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.entity import EntityDescription
from pytoyoda.models.endpoints.climate import (
    ACOperations,
    ACParameters,
    ClimateControlModel,
    ClimateSettingsModel,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .entity import ToyotaBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota climate entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    description = EntityDescription(
        key="climate",
        name="Climate",
    )

    entities = [
        ToyotaClimate(coordinator, entry.entry_id, index, description)
        for index in range(len(coordinator.data))
    ]
    async_add_entities(entities)


class ToyotaClimate(ToyotaBaseEntity, ClimateEntity):
    """Representation of a Toyota climate control."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = (HVACMode.OFF, HVACMode.HEAT_COOL)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_min_temp = 18
    _attr_max_temp = 29
    _attr_target_temperature_step = 1
    _attr_preset_modes = ("none", "front_defrost", "rear_defrost", "both_defrost")

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        vehicle_index: int,
        description: EntityDescription,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 21
        self._attr_preset_mode = "none"

    def _create_climate_settings(
        self,
        *,
        temperature: float | None = None,
        settings_on: bool | None = None,
        front_defrost: bool | None = None,
        rear_defrost: bool | None = None,
    ) -> ClimateSettingsModel:
        """Create a ClimateSettingsModel with current or provided values.

        Args:
            temperature: Temperature to set, or None to use current
            settings_on: Whether climate should be on, or None to use current
            front_defrost: Whether to en/disable front defrost, or None to use current
            rear_defrost: Whether to en/disable rear defrost, or None to use current

        Returns:
            ClimateSettingsModel configured with the specified settings
        """
        current_settings = self.vehicle.climate_settings

        # Get current defrost settings if not explicitly provided
        current_front_defrost = False
        current_rear_defrost = False

        for operation in current_settings.operations:
            if operation.category_name == "defrost":
                for param in operation.parameters:
                    if param.name == "frontDefrost":
                        current_front_defrost = param.enabled
                    elif param.name == "rearDefrost":
                        current_rear_defrost = param.enabled

        # Use provided values or fall back to current settings
        final_front_defrost = (
            front_defrost if front_defrost is not None else current_front_defrost
        )
        final_rear_defrost = (
            rear_defrost if rear_defrost is not None else current_rear_defrost
        )

        return ClimateSettingsModel(
            settingsOn=settings_on
            if settings_on is not None
            else current_settings.settings_on,
            temperature=temperature
            if temperature is not None
            else current_settings.temperature.value,
            temperatureUnit="C",
            acOperations=[
                ACOperations(
                    categoryName="defrost",
                    acParameters=[
                        ACParameters(enabled=final_front_defrost, name="frontDefrost"),
                        ACParameters(enabled=final_rear_defrost, name="rearDefrost"),
                    ],
                )
            ],
        )

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation mode."""
        return self._attr_hvac_mode

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self._attr_target_temperature

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        return self._attr_preset_mode

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        try:
            # Determine defrost settings based on preset
            front_enabled = preset_mode in ["front_defrost", "both_defrost"]
            rear_enabled = preset_mode in ["rear_defrost", "both_defrost"]

            climate_settings = self._create_climate_settings(
                front_defrost=front_enabled,
                rear_defrost=rear_enabled,
            )

            # Send update
            if await self._send_climate_settings(
                climate_settings, f"set defrost preset to {preset_mode}"
            ):
                self._attr_preset_mode = preset_mode
                self.async_write_ha_state()

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error setting preset mode")

    async def async_update(self) -> None:
        """Update climate settings from the car."""
        try:
            await self.vehicle.refresh_climate_status()
        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error updating climate settings")

    async def _send_climate_settings(
        self, climate_settings: ClimateSettingsModel, action_description: str
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
                status = await self.vehicle._api.update_climate_settings(  # noqa: SLF001
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
            else:
                _LOGGER.info(
                    "%s updated for %s (will apply when turned on)",
                    action_description.capitalize(),
                    self.vehicle.alias,
                )
        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error during %s", action_description)
            return False

        return True

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            await self._turn_off_climate()
        elif hvac_mode == HVACMode.HEAT_COOL:
            await self._turn_on_climate()

    async def async_set_temperature(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        try:
            climate_settings = self._create_climate_settings(temperature=temperature)

            # Send update
            if await self._send_climate_settings(
                climate_settings, f"set temperature to {temperature}°C"
            ):
                self._attr_target_temperature = temperature
                self.async_write_ha_state()

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error setting climate temperature")

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

            climate_settings = self._create_climate_settings(settings_on=True)

            _LOGGER.info(
                "Updating climate settings for %s: temperature=%s",
                self.vehicle.alias,
                climate_settings.temperature,
            )

            # First, update the settings
            status = await self.vehicle._api.update_climate_settings(  # noqa: SLF001
                self.vehicle.vin, climate_settings
            )

            _LOGGER.debug("Update settings response: %s", status)

            # Now send the engine-start command to actually turn on climate
            _LOGGER.info("Sending engine-start command to %s", self.vehicle.alias)

            if await self.vehicle._api.send_climate_control_command(  # noqa: SLF001
                self.vehicle.vin, ClimateControlModel(command="engine-start")
            ):
                self._attr_hvac_mode = HVACMode.HEAT_COOL
                self.async_write_ha_state()

                _LOGGER.info("Climate control turned on for %s", self.vehicle.alias)

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error turning on climate")

    async def _turn_off_climate(self) -> None:
        """Turn off the climate control."""
        try:
            _LOGGER.info("Attempting to turn off climate for %s", self.vehicle.alias)

            # Send the engine-stop command to turn off climate
            if await self.vehicle._api.send_climate_control_command(  # noqa: SLF001
                self.vehicle.vin, ClimateControlModel(command="engine-stop")
            ):
                self._attr_hvac_mode = HVACMode.OFF
                self.async_write_ha_state()

                _LOGGER.info("Climate control turned off for %s", self.vehicle.alias)

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error turning off climate")
