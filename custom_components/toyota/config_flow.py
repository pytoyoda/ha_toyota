"""Config flow for Toyota Connected Services integration."""

# pylint: disable=W0212, W0511

import asyncio
import logging
import os
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from pytoyoda.client import MyT
from pytoyoda.exceptions import ToyotaInvalidUsernameError, ToyotaLoginError

from .const import CONF_BRAND, CONF_METRIC_VALUES, DOMAIN

_LOGGER = logging.getLogger(__name__)

BRAND_OPTIONS = {
    "toyota": "Toyota",
    "lexus": "Lexus",
}

# Map user-friendly brand names to API codes
BRAND_API_MAP = {
    "toyota": "T",
    "lexus": "L",
}


@contextmanager
def _writable_cwd(path: str) -> Generator[None]:
    """Temporarily change cwd so pytoyoda/hishel can create its cache."""
    (Path(path) / ".cache" / "hishel").mkdir(parents=True, exist_ok=True)
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


class ToyotaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # pylint: disable=W0223
    """Handle a config flow for Toyota Connected Services."""

    VERSION = 1

    def __init__(self) -> None:
        """Start the toyota custom component config flow."""
        self._reauth_entry = None
        self._email = None
        self._metric_values = True
        self._brand = "toyota"

    async def async_step_user(self, user_input: dict | None = None) -> Any:  # noqa : ANN401
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._metric_values = user_input[CONF_METRIC_VALUES]
            self._brand = user_input[CONF_BRAND]
            unique_id = f"{user_input[CONF_EMAIL].lower()}_{self._brand}"

            # Convert brand selection to API code
            brand_code = BRAND_API_MAP.get(self._brand, "T")

            _LOGGER.info(
                "Testing login for %s (brand code: %s)", self._brand, brand_code
            )

            client = MyT(
                username=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                brand=brand_code,  # Pass brand code to API client
            )

            await self.async_set_unique_id(unique_id)
            if not self._reauth_entry:
                self._abort_if_unique_id_configured()
            config_dir = self.hass.config.config_dir

            def _login() -> None:
                with _writable_cwd(config_dir):
                    asyncio.run(client.login())

            try:
                await self.hass.async_add_executor_job(_login)
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
                    entry_title = (
                        f"{BRAND_OPTIONS[self._brand]} - {user_input[CONF_EMAIL]}",
                    )
                    return self.async_create_entry(
                        title=entry_title,
                        data=user_input,
                    )
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data=user_input,
                    unique_id=unique_id,
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
                    vol.Required(
                        CONF_BRAND, default=self._brand
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=key, label=value)
                                for key, value in BRAND_OPTIONS.items()
                            ],
                            translation_key=CONF_BRAND,
                        ),
                    ),
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
        self._brand = entry_data.get(CONF_BRAND, "toyota")
        return await self.async_step_user()
