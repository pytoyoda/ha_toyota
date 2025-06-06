"""Config flow for Toyota Connected Services integration."""

# pylint: disable=W0212, W0511

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from pytoyoda.client import MyT
from pytoyoda.exceptions import ToyotaInvalidUsernameError, ToyotaLoginError

from .const import CONF_METRIC_VALUES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ToyotaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # pylint: disable=W0223
    """Handle a config flow for Toyota Connected Services."""

    VERSION = 1

    def __init__(self) -> None:
        """Start the toyota custom component config flow."""
        self._reauth_entry = None
        self._email = None
        self._metric_values = True

    async def async_step_user(self, user_input: dict | None = None) -> Any:  # noqa : ANN401
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._metric_values = user_input[CONF_METRIC_VALUES]
            unique_id = user_input[CONF_EMAIL].lower()

            client = MyT(
                username=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
            )

            await self.async_set_unique_id(unique_id)
            if not self._reauth_entry:
                self._abort_if_unique_id_configured()
            try:
                await client.login()
            except ToyotaLoginError:
                errors["base"] = "invalid_auth"
                _LOGGER.exception("Toyota login error: Invalid auth")
            except ToyotaInvalidUsernameError:
                errors["base"] = "invalid_username"
                _LOGGER.exception("Toyota login error: Invalid username")
            except Exception:  # pylint: disable=broad-except
                errors["base"] = "unknown"
                _LOGGER.exception("An unknown error occurred during login request.")
            else:
                if not self._reauth_entry:
                    return self.async_create_entry(
                        title=user_input[CONF_EMAIL], data=user_input
                    )
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry, data=user_input, unique_id=unique_id
                )
                # Reload the config entry otherwise devices will remain unavailable
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._email): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_METRIC_VALUES, default=self._metric_values
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Perform reauth if the user credentials have changed."""
        if "entry_id" in self.context:
            self._reauth_entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )
        else:
            self._reauth_entry = None
        self._email = entry_data[CONF_EMAIL]
        self._metric_values = entry_data[CONF_METRIC_VALUES]
        return await self.async_step_user()
