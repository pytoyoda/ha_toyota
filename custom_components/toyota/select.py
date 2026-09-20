"""Toyota Connected Services seat-heater level controls.

Seat-heater levels are already surfaced read-only via the climate entity's
extra_state_attributes; these entities make them controllable. Toyota's
remote API has no settings-only write, so changing a level sends a full
climate-control ``start`` (see ``async_apply_climate_settings`` in
``climate.py``) - this also (re)starts remote climate control, mirroring the
MyToyota app's own behavior for these controls.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .climate import _vehicle_has_climate_capability, async_apply_climate_settings
from .const import DOMAIN
from .entity import ToyotaBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import VehicleData

_LOGGER = logging.getLogger(__name__)

SEAT_HEATER_OPTIONS = ("off", "low", "medium", "high")

# (SeatOptionsModel field name, entity/translation key, friendly name)
_SEAT_HEATERS = (
    ("driver_seat", "seat_heater_driver", "Driver seat heater"),
    ("passenger_seat", "seat_heater_passenger", "Passenger seat heater"),
    (
        "rear_driver_seat",
        "seat_heater_rear_driver",
        "Rear driver seat heater",
    ),
    (
        "rear_passenger_seat",
        "seat_heater_rear_passenger",
        "Rear passenger seat heater",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Toyota seat-heater select entities."""
    coordinator: DataUpdateCoordinator[list[VehicleData]] = hass.data[DOMAIN][
        entry.entry_id
    ]

    entities: list[ToyotaSeatHeaterSelect] = []
    for index, vehicle_data in enumerate(coordinator.data):
        vehicle = vehicle_data["data"]
        if not _vehicle_has_climate_capability(vehicle):
            continue
        seats = getattr(
            getattr(vehicle, "climate_settings", None), "seat_options", None
        )
        if seats is None:
            continue
        for field, key, name in _SEAT_HEATERS:
            if getattr(seats, field, None) is None:
                continue
            entities.append(
                ToyotaSeatHeaterSelect(
                    coordinator,
                    entry.entry_id,
                    index,
                    SelectEntityDescription(
                        key=key,
                        translation_key=key,
                        name=name,
                        icon="mdi:car-seat-heater",
                    ),
                    field,
                )
            )
    async_add_entities(entities)


class ToyotaSeatHeaterSelect(ToyotaBaseEntity, SelectEntity):
    """Per-seat heater level control (off/low/medium/high)."""

    _attr_options: ClassVar[list[str]] = list(SEAT_HEATER_OPTIONS)

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[list[VehicleData]],
        entry_id: str,
        vehicle_index: int,
        description: SelectEntityDescription,
        seat_field: str,
    ) -> None:
        """Initialize the seat-heater select entity."""
        super().__init__(coordinator, entry_id, vehicle_index, description)
        self._seat_field = seat_field
        # Optimistic write-through: holds the just-requested level until the
        # coordinator's next read confirms it, so the tile doesn't flicker
        # back to the pre-change value while Toyota's backend catches up.
        self._pending_option: str | None = None

    def _read_option(self) -> str | None:
        """Read the seat's current heater level from climate_settings."""
        seats = getattr(
            getattr(self.vehicle, "climate_settings", None), "seat_options", None
        )
        value = getattr(seats, self._seat_field, None)
        return value if value in SEAT_HEATER_OPTIONS else None

    @property
    def current_option(self) -> str | None:
        """Return the current seat heater level."""
        if self._pending_option is not None:
            return self._pending_option
        return self._read_option()

    async def async_select_option(self, option: str) -> None:
        """Set a new seat heater level (sends a climate-control start)."""
        previous = self._pending_option
        self._pending_option = option
        self.async_write_ha_state()
        try:
            await async_apply_climate_settings(
                self.vehicle, seat_overrides={self._seat_field: option}
            )
        except Exception as err:
            self._pending_option = previous
            self.async_write_ha_state()
            if isinstance(err, HomeAssistantError):
                raise
            msg = f"Failed to set {self.entity_description.name}: {err}"
            raise HomeAssistantError(msg) from err

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()
        if self._pending_option is not None and (
            self._read_option() == self._pending_option
        ):
            self._pending_option = None
