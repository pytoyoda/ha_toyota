"""Tests for the Toyota EU community integration config flow."""

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import BooleanSelector

from custom_components.toyota.const import CONF_METRIC_VALUES, DOMAIN

async def test_form(hass):
    """Assert we get the user form with correct data_schema."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    print(result)
    print(type(result["data_schema"].schema[CONF_EMAIL]))
    print(type(result["data_schema"].schema[CONF_PASSWORD]))
    print(type(result["data_schema"].schema[CONF_METRIC_VALUES]))

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["handler"] == DOMAIN
