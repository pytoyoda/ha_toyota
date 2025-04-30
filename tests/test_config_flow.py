"""Tests for the Toyota EU community integration config flow."""

import pytest

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import BooleanSelector

from custom_components.toyota.const import CONF_METRIC_VALUES, DOMAIN
from pytoyoda.exceptions import ToyotaInvalidUsernameError

async def test_form(hass):
    """Assert we get the user form with correct data_schema."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["handler"] == DOMAIN
    assert isinstance(result["data_schema"].schema[CONF_EMAIL], type)
    assert isinstance(result["data_schema"].schema[CONF_PASSWORD], type)
    assert isinstance(result["data_schema"].schema[CONF_METRIC_VALUES], BooleanSelector)

async def test_form_no_email(hass):
    """Assert we get the a ToyotaInvalidUsernameError for empty EMail."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["handler"] == DOMAIN

    with pytest.raises(ToyotaInvalidUsernameError):
        await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={
            CONF_EMAIL: "",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True
            }
    )

async def test_form_no_password(hass):
    """Assert we get the a ToyotaInvalidUsernameError for empty EMail."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["handler"] == DOMAIN

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={
            CONF_EMAIL: "test@mail.com",
            CONF_PASSWORD: "",
            CONF_METRIC_VALUES: True
            }
    )
    print(result)
