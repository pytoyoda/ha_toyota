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
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from pytoyoda.models.endpoints.climate import (
    HeatingOptionsModel,
    SeatOptionsModel,
    V2RemoteClimateControlRequestModel,
)
from pytoyoda.models.endpoints.common import UnitValueModel

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
    from pytoyoda.models.vehicle import Vehicle

from .const import DOMAIN
from .entity import ToyotaBaseEntity

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=120)

# Command success code in the V2 climate-control response payload.
CLIMATE_COMMAND_OK = "000000"

# The /v1/vehicle/climate-settings payload no longer carries min/max/step; mirror
# the MyToyota app's fixed 18-29 degree range with a 1-degree step.
DEFAULT_MIN_TEMP = 18
DEFAULT_MAX_TEMP = 29
DEFAULT_TEMP_STEP = 1


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

    entities = []
    for index, vehicle_data in enumerate(coordinator.data):
        if _vehicle_has_climate_capability(vehicle_data["data"]):
            entities.append(
                ToyotaClimate(coordinator, entry.entry_id, index, description)
            )
    async_add_entities(entities)


def _vehicle_has_climate_capability(vehicle: Vehicle) -> bool:
    """Check if vehicle supports climate control."""
    try:
        # Standard path (ICE / hybrid, e.g. Corolla): legacy feature flag.
        if getattr(
            getattr(vehicle._vehicle_info, "features", False),  # noqa : SLF001
            "climate_start_engine",
            False,
        ):
            return True
        # PHEV / EV path: extended capabilities (added upstream in ea73031).
        caps = getattr(vehicle._vehicle_info, "extended_capabilities", False)  # noqa : SLF001
        for cap in [
            "climate_capable",
            "econnect_climate_capable",
            "remote_engine_start_stop",
        ]:
            if getattr(caps, cap, False):
                return True
    except Exception:  # pylint: disable=W0718 # noqa : BLE001
        return False

    return False


class ToyotaClimate(ToyotaBaseEntity, ClimateEntity):
    """Representation of a Toyota climate control."""

    _attr_translation_key = "climate"
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

        # Initialize with defaults first
        self._attr_target_temperature = 21
        self._attr_min_temp = 18
        self._attr_max_temp = 29
        self._attr_target_temperature_step = 1
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_front_defrost = False
        self._attr_rear_defrost = False
        self._attr_current_temperature = None
        self._attr_climate_status = False

        # User-set target-temp / defrost are applied on the next climate START
        # (Tier A sends the full desired body). This flag marks them dirty so a
        # coordinator poll can't overwrite them with the car's saved values before
        # the start lands; it's cleared once a start is confirmed.
        self._settings_dirty = False

        # Load settings from coordinator if available
        self._load_climate_settings_from_coordinator()

    def _load_climate_settings_from_coordinator(self) -> None:
        """Load climate settings from coordinator data if available."""
        try:
            if not self.vehicle or not getattr(self.vehicle, "climate_settings", None):
                _LOGGER.debug("Vehicle climate_settings not yet available")
                return

            # Update temperature settings
            self._load_temperature_settings()

            # Read defrost settings from operations
            self._load_defrost_settings()

            _LOGGER.debug(
                "Loaded climate settings for %s: temp=%s, min=%s, max=%s",
                getattr(self.vehicle, "alias", "vehicle"),
                self._attr_target_temperature,
                self._attr_min_temp,
                self._attr_max_temp,
            )
        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error loading climate settings from coordinator")

    def _load_temperature_settings(self) -> None:
        """Load target temperature + unit from climate_settings."""
        # Don't clobber a user-set target the user hasn't started yet.
        if self._settings_dirty:
            return
        climate_settings = self.vehicle.climate_settings
        target_temperature = climate_settings.temperature
        if target_temperature is not None and target_temperature.value is not None:
            self._attr_target_temperature = target_temperature.value
            # Honor the unit the car reports rather than assuming Celsius.
            unit = (target_temperature.unit or "").upper()
            self._attr_temperature_unit = (
                UnitOfTemperature.FAHRENHEIT
                if unit.startswith("F")
                else UnitOfTemperature.CELSIUS
            )
        # The new climate-settings payload no longer carries min/max/step; use the
        # app's fixed bounds (a None min/max would make HA core's set_temperature
        # validation do `float < None` -> TypeError).
        self._attr_min_temp = DEFAULT_MIN_TEMP
        self._attr_max_temp = DEFAULT_MAX_TEMP
        self._attr_target_temperature_step = DEFAULT_TEMP_STEP

    def _load_defrost_settings(self) -> None:
        """Load defrost/defogger state from climate_settings heating options."""
        # Don't clobber a user-set preset the user hasn't started yet.
        if self._settings_dirty:
            return
        # Migrated 2026-07: defrost state moved from the old acOperations list to
        # the new heatingOptions map. heating_options can be None (climate-settings
        # 403/500), so guard before reading.
        heating = getattr(self.vehicle.climate_settings, "heating_options", None)
        if heating is None:
            return
        if heating.front_defroster is not None:
            self._attr_front_defrost = heating.front_defroster
        if heating.rear_defogger is not None:
            self._attr_rear_defrost = heating.rear_defogger

    def _load_climate_status_from_coordinator(self) -> None:
        """Reflect the car's live climate state (on/off + cabin temp) if available.

        The coordinator fetches climate_status each cycle; reading it here keeps the
        entity truthful even when climate is started/stopped from the Toyota app.
        """
        try:
            climate_status = getattr(self.vehicle, "climate_status", None)
            if climate_status is None:
                return
            is_on = climate_status.is_on
            if is_on is not None:
                self._attr_climate_status = is_on
                self._attr_hvac_mode = HVACMode.HEAT_COOL if is_on else HVACMode.OFF
            current = climate_status.current_temperature
            self._attr_current_temperature = (
                current.value if current is not None else None
            )
        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error loading climate status from coordinator")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Surface the new climate capabilities (read-only until the V2 PR).

        Seat heaters are multi-level (off/low/medium/high); steering heater and the
        defroster/defogger are on/off. These are not yet writable entities.
        """
        settings = getattr(self.vehicle, "climate_settings", None)
        if settings is None:
            return None
        heating = settings.heating_options
        seats = settings.seat_options
        attrs: dict[str, Any] = {}
        if heating is not None:
            attrs["steering_heater"] = heating.steering_heater
        if seats is not None:
            attrs["seat_heater_driver"] = seats.driver_seat
            attrs["seat_heater_passenger"] = seats.passenger_seat
            attrs["seat_heater_rear_driver"] = seats.rear_driver_seat
            attrs["seat_heater_rear_passenger"] = seats.rear_passenger_seat
        if settings.duration is not None:
            attrs["duration_minutes"] = int(settings.duration.total_seconds() // 60)
        return attrs or None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._load_climate_settings_from_coordinator()
        self._load_climate_status_from_coordinator()
        super()._handle_coordinator_update()

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
    def front_defrost(self) -> bool:
        """Return front_defrost."""
        return self._attr_front_defrost

    @property
    def rear_defrost(self) -> bool:
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

    def _build_start_request(self) -> V2RemoteClimateControlRequestModel:
        """Assemble the V2 ``start`` body from current entity + read state.

        Front/rear defroster come from the entity's preset state; steering + per-seat
        heaters are **echoed** from the current climate-settings read (never invented)
        so a start doesn't change them.
        """
        settings = getattr(self.vehicle, "climate_settings", None)
        read_heating = getattr(settings, "heating_options", None)
        read_seats = getattr(settings, "seat_options", None)

        def _wire(*, flag: bool | None) -> str | None:
            return None if flag is None else ("on" if flag else "off")

        heating = HeatingOptionsModel(
            front_defroster=_wire(flag=self.front_defrost),
            rear_defogger=_wire(flag=self.rear_defrost),
            # Steering: echo the car's current value so a start doesn't change it.
            # The read is ALREADY an "on"/"off" string, so pass it through raw — do
            # NOT _wire() it (that turned "off" into "on", silently switching the
            # wheel heater on with every start).
            steering_heater=getattr(read_heating, "steering_heater", None),
        )
        seats = None
        if read_seats is not None:
            seats = SeatOptionsModel(
                driver_seat=read_seats.driver_seat,
                passenger_seat=read_seats.passenger_seat,
                rear_driver_seat=read_seats.rear_driver_seat,
                rear_passenger_seat=read_seats.rear_passenger_seat,
            )

        unit = (
            "F" if self._attr_temperature_unit == UnitOfTemperature.FAHRENHEIT else "C"
        )
        # Only persist defaults when we have a full read to echo — a null-options
        # body with save_settings=True could clear the car's saved seat/steering.
        save = read_seats is not None and read_heating is not None
        return V2RemoteClimateControlRequestModel(
            command="start",
            temperature=UnitValueModel(unit=unit, value=self.target_temperature),
            heating_options=heating,
            seat_options=seats,
            save_settings=save,
        )

    @staticmethod
    def _command_ok(response: object) -> bool:
        """Whether a V2 climate-control response reported command success."""
        payload = getattr(response, "payload", None)
        return payload is not None and payload.return_code == CLIMATE_COMMAND_OK

    async def _poll_status(self) -> None:
        """Wake + refetch climate_status and reflect it on the entity."""
        if await self.vehicle.refresh_climate_status():
            await self.vehicle.update(only=["climate_status"])
            self._load_climate_status_from_coordinator()
            self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        try:
            # Update the underlying defrost attributes based on preset mode
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

            # Applied on the next start (V2 sends the full desired body); no
            # standalone settings write in Tier A. Mark dirty so a coordinator poll
            # doesn't revert it before the start.
            self._settings_dirty = True
            self.async_write_ha_state()

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error setting preset mode")

    async def async_update(self) -> None:
        """Poll the car for fresh climate status (when climate is on)."""
        if not self.climate_settings_on:
            return
        try:
            await self._poll_status()
        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error updating climate status")

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
            # Local desired state; applied on the next start (V2 sends the full body).
            # Mark dirty so a coordinator poll doesn't revert it before the start.
            self._attr_target_temperature = temperature
            self._settings_dirty = True
            self.async_write_ha_state()

        except Exception:  # pylint: disable=W0718
            _LOGGER.exception("Error setting climate temperature")

    async def async_turn_on(self) -> None:
        """Turn on climate control."""
        await self._turn_on_climate()

    async def async_turn_off(self) -> None:
        """Turn off climate control."""
        await self._turn_off_climate()

    async def _send_start(self) -> None:
        """Send one V2 ``start`` with the current desired body; raise on rejection.

        Keeps the request-build + success-check in one place.
        """
        response = await self.vehicle.set_climate(self._build_start_request())
        ok = self._command_ok(response)
        if not ok:
            _LOGGER.debug("Climate start rejected: %s", response)
            msg = (
                "Toyota did not start the climate. Common causes: the car is "
                "unlocked, a door/window/trunk is open, or a key is inside. "
                "(Re-issuing a start while climate is already running is also "
                "rejected.)"
            )
            raise HomeAssistantError(msg)
        # The desired settings were accepted (and saved) — safe to resume seeding
        # target-temp / defrost from the coordinator read again.
        self._settings_dirty = False

    async def _turn_on_climate(self) -> None:
        """Turn on climate via a single V2 ``start`` command."""
        # Optimistically turn on; rolled back if the car rejects the start.
        self._attr_hvac_mode = HVACMode.HEAT_COOL
        self.async_write_ha_state()

        _LOGGER.debug("Attempting to turn on climate for %s", self.vehicle.alias)
        try:
            await self._send_start()
        except Exception as err:  # pylint: disable=W0718
            # Roll back the optimistic "on" so the tile reflects reality.
            self._attr_hvac_mode = HVACMode.OFF
            self.async_write_ha_state()
            if isinstance(err, HomeAssistantError):
                raise
            msg = f"Failed to turn on Toyota climate: {err}"
            raise HomeAssistantError(msg) from err

        _LOGGER.debug("Climate control turned on for %s", self.vehicle.alias)
        # Confirm the actual state (stopped -> starting/running) best-effort.
        try:
            await self._poll_status()
        except Exception:  # noqa: BLE001  # best-effort poll; any failure is non-fatal
            _LOGGER.debug("Post-start status poll failed (non-fatal)", exc_info=True)

    async def _turn_off_climate(self) -> None:
        """Turn off climate via a single V2 ``stop`` command."""
        # Optimistically turn off; the coordinator reconciles actual state on poll.
        self._attr_hvac_mode = HVACMode.OFF
        self.async_write_ha_state()

        _LOGGER.debug("Attempting to turn off climate for %s", self.vehicle.alias)
        try:
            response = await self.vehicle.set_climate(
                V2RemoteClimateControlRequestModel(command="stop"),
            )
        except Exception as err:  # pylint: disable=W0718
            # The stop may not have landed — revert to "on" rather than falsely off.
            self._attr_hvac_mode = HVACMode.HEAT_COOL
            self.async_write_ha_state()
            msg = f"Failed to turn off Toyota climate: {err}"
            raise HomeAssistantError(msg) from err

        # A non-000000 on stop is usually benign ("already stopped"); don't error the
        # tile — the next coordinator poll reconciles the real state via is_on.
        if not self._command_ok(response):
            _LOGGER.debug("Climate stop returned non-success: %s", response)
        _LOGGER.debug("Climate control turned off for %s", self.vehicle.alias)
