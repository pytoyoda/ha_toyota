"""Toyota Connected Services Climate Control."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.entity import EntityDescription
from pytoyoda.models.endpoints.climate import (
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
SCAN_INTERVAL = timedelta(seconds=120)


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
        self._attr_target_temperature = self.vehicle.climate_settings.get(
            "temperature", 21
        )
        self._attr_min_temp = self.vehicle.climate_settings.get("min_temp", 18)
        self._attr_max_temp = self.vehicle.climate_settings.get("max_temp", 29)
        self._attr_target_temperature_step = self.vehicle.climate_settings.get(
            "temp_interval", 1
        )
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_current_temperature = None
        self._attr_climate_status = False

        for operation in self.defrost_ac_operations:
            for param in operation.parameters:
                if param.name == "frontDefrost":
                    self._attr_front_defrost = param.enabled
                elif param.name == "rearDefrost":
                    self._attr_rear_defrost = param.enabled

    @property
    def should_poll(self) -> bool:
        """Return True to enable polling."""
        return True

    @property
    def climate_settings_on(self) -> bool | None:
        """Return settingsOn based on HVACMode."""
        return self.hvac_mode == HVACMode.HEAT_COOL

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation mode."""
        return self._attr_hvac_mode

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._attr_current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self._attr_target_temperature

    @property
    def front_defrost(self) -> bool | None:
        """Return front_defrost."""
        return self._attr_front_defrost

    @property
    def rear_defrost(self) -> bool | None:
        """Return rear_defrost."""
        return self._attr_rear_defrost

    @property
    def preset_mode(self) -> str:
        """Return the current preset mode."""
        if self.front_defrost and self.rear_defrost:
            return "both_defrost"
        if self.front_defrost:
            return "front_defrost"
        if self.rear_defrost:
            return "rear_defrost"
        return "none"

    @property
    def defrost_ac_operations(self) -> list:
        """Return the currnt defrost AC operations."""
        return [
            op for op in self.vehicle.climate_settings.operations
            if op.category_name == "defrost"
        ]

    def _create_climate_settings(self) -> ClimateSettingsModel:
        """Create a ClimateSettingsModel with current or provided values.

        Returns:
            ClimateSettingsModel configured with the specified settings
        """
        for operation in self.defrost_ac_operations:
            for param in operation.parameters:
                if param.name == "frontDefrost":
                    param.enabled = self.front_defrost
                elif param.name == "rearDefrost":
                    param.enabled = self.rear_defrost

        return ClimateSettingsModel(
            settingsOn=self.climate_settings_on,
            temperature=self.target_temperature,
            temperatureUnit="C",
            acOperations=self.vehicle.climate_settings.operations,
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        try:
            if preset_mode == "both_defrost":
                self._attr_front_defrost = True
                self._attr_rear_defrost = True
            elif preset_mode == "front_defrost":
                self._attr_front_defrost = True
                self._attr_rear_defrost = False
            elif preset_mode == "rear_defrost":
                self._attr_front_defrost = False
                self._attr_rear_defrost = True
            else:  # "none"
                self._attr_front_defrost = False
                self._attr_rear_defrost = False

            self.async_write_ha_state()
            await self._send_climate_settings()

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error setting preset mode")

    async def async_update(self) -> None:
        """Update climate settings from the car."""
        if not self.climate_settings_on:
            return

        try:
            if await self.vehicle.refresh_climate_status():
                _LOGGER.debug("Climate status refreshed from car")
                # vehicle.climate_status does not seem to work for some reason
                response = await self.vehicle._api.get_climate_status(  # noqa: SLF001
                    self.vehicle.vin
                )
                _LOGGER.debug("Climate status fetched %s", response)
                climate_status = response.payload
                if climate_status.status:
                    _LOGGER.debug("Climate is on, sync current temperature")
                    # car has started heating
                    self._attr_climate_status = True
                    self._attr_current_temperature = (
                        climate_status.current_temperature.value
                    )

                elif self._attr_climate_status:
                    _LOGGER.debug("Climate is now off")
                    # turn off the climate device
                    self._attr_hvac_mode = HVACMode.OFF
                    self._attr_current_temperature = None
                    # reset the climate status flag
                    self._attr_climate_status = False

                self.async_write_ha_state()

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error updating climate settings")

    async def _send_climate_settings(self) -> bool:
        """Send climate settings to car if climate is on.

        Returns:
            True if settings were sent (or climate was off), False on error
        """
        try:
            # Only send to API if climate is on
            if self.climate_settings_on:
                climate_settings = self._create_climate_settings()
                _LOGGER.debug("Sending climate settings to car: %s", climate_settings)
                status = await self.vehicle._api.update_climate_settings(  # noqa: SLF001
                    self.vehicle.vin, climate_settings
                )

                _LOGGER.debug("API response status: %s", status)

                # Check if the update was successful
                if not status or (hasattr(status, "status") and status.status == 0):
                    _LOGGER.exception("Failed to send climate settings")
                    return False

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error sending climate settings")
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
            self._attr_target_temperature = temperature
            self.async_write_ha_state()

            await self._send_climate_settings()

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
            # optimistically turn on the climate device
            self._attr_hvac_mode = HVACMode.HEAT_COOL
            self.async_write_ha_state()

            _LOGGER.debug("Attempting to turn on climate for %s", self.vehicle.alias)

            # First, update the settings
            if await self._send_climate_settings():
                # Now send the engine-start command to actually turn on climate
                _LOGGER.debug("Sending engine-start command to %s", self.vehicle.alias)

                status = await self.vehicle._api.send_climate_control_command(  # noqa: SLF001
                    self.vehicle.vin, ClimateControlModel(command="engine-start")
                )

                # Check if the update was successful
                if not status or (hasattr(status, "status") and status.status == 0):
                    _LOGGER.debug("Failed to start engine: %s", status)
                    # The official app sends a notification to the user
                    # Should we send a notification to the user?
                    # Potential reasons:
                    # Car unreachable
                    # Car is unlocked
                    # One or more windows, doors or trunk open
                    # Key detected inside the car
                    # Climate was already started once for 20 minutes since
                    # last engine ignition
                    self._attr_hvac_mode = HVACMode.OFF
                    self.async_write_ha_state()

                else:
                    _LOGGER.debug(
                        "Climate control turned on for %s", self.vehicle.alias
                    )

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error turning on climate")

    async def _turn_off_climate(self) -> None:
        """Turn off the climate control."""
        try:
            # optimistically turn off the climate device
            self._attr_hvac_mode = HVACMode.OFF
            self.async_write_ha_state()

            _LOGGER.debug("Attempting to turn off climate for %s", self.vehicle.alias)

            # Send the engine-stop command to turn off climate
            if await self.vehicle._api.send_climate_control_command(  # noqa: SLF001
                self.vehicle.vin, ClimateControlModel(command="engine-stop")
            ):
                _LOGGER.debug("Climate control turned off for %s", self.vehicle.alias)

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error turning off climate")
