"""Tests for the Toyota EU community integration config flow."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytoyoda.exceptions import ToyotaLoginError

from custom_components.toyota.const import CONF_METRIC_VALUES, DOMAIN

async def test_form(hass):
    """Test giving bad config dat to REST API config flow."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    print(result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
