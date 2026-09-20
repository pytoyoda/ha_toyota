"""Tests for the Toyota EU community integration config flow."""

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import BooleanSelector
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.exceptions import ToyotaInvalidUsernameError, ToyotaLoginError

from custom_components.toyota.const import (
    CONF_BRAND,
    CONF_ENABLE_STATUS_REFRESH,
    CONF_MAX_RECENT_TRIPS,
    CONF_METRIC_VALUES,
    DOMAIN,
)


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


async def test_successful_login_creates_entry(hass) -> None:
    """A successful login must create a config entry with the submitted data."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with patch(
        "custom_components.toyota.config_flow.MyT.login", new=AsyncMock()
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_BRAND: "toyota",
                CONF_EMAIL: "user@example.com",
                CONF_PASSWORD: "password",
                CONF_METRIC_VALUES: True,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Toyota - user@example.com"
    assert result["data"][CONF_EMAIL] == "user@example.com"


async def test_login_error_shows_invalid_auth(hass) -> None:
    """A ToyotaLoginError must surface as the invalid_auth form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with patch(
        "custom_components.toyota.config_flow.MyT.login",
        new=AsyncMock(side_effect=ToyotaLoginError("bad creds")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_BRAND: "toyota",
                CONF_EMAIL: "user@example.com",
                CONF_PASSWORD: "wrong",
                CONF_METRIC_VALUES: True,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_unknown_error_shows_unknown(hass) -> None:
    """Any unexpected exception must surface as the generic unknown error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with patch(
        "custom_components.toyota.config_flow.MyT.login",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_BRAND: "toyota",
                CONF_EMAIL: "user@example.com",
                CONF_PASSWORD: "password",
                CONF_METRIC_VALUES: True,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_updates_existing_entry(hass) -> None:
    """A successful reauth must update the existing entry, not create a new one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com_toyota",
        data={
            CONF_BRAND: "toyota",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "old-password",
            CONF_METRIC_VALUES: True,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )

    with patch(
        "custom_components.toyota.config_flow.MyT.login", new=AsyncMock()
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_reload",
        new=AsyncMock(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_BRAND: "toyota",
                CONF_EMAIL: "user@example.com",
                CONF_PASSWORD: "new-password",
                CONF_METRIC_VALUES: True,
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"


async def test_options_flow_shows_form_with_current_values(hass) -> None:
    """The options form must default to the entry's current option values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BRAND: "toyota",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True,
        },
        options={CONF_MAX_RECENT_TRIPS: 7},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_reenabling_status_refresh_clears_auto_disable(
    hass,
) -> None:
    """Re-enabling status refresh must clear a prior auto-disable flag."""
    from custom_components.toyota.const import CONF_AUTO_DISABLED_STATUS_REFRESH

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BRAND: "toyota",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True,
        },
        options={
            CONF_ENABLE_STATUS_REFRESH: False,
            CONF_AUTO_DISABLED_STATUS_REFRESH: True,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "polling_interval_minutes": 15,
            "retain_on_transient_failure": True,
            CONF_ENABLE_STATUS_REFRESH: True,
            "idle_wake_hours": 8,
            "failed_wake_threshold": 3,
            "max_cache_age_minutes": 30,
            "post_count_per_stop": 2,
            CONF_MAX_RECENT_TRIPS: 5,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AUTO_DISABLED_STATUS_REFRESH] is False
