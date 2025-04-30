"""Tests for the Toyota EU community integration config flow."""

from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytoyoda.exceptions import ToyotaLoginError

from custom_components.toyota.const import CONF_METRIC_VALUES, DOMAIN

async def test_form(hass, mock_toyota_client):
    """Test we get the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}

    # Test form validation
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EMAIL: "",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"email": "email_required"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "",
            CONF_METRIC_VALUES: True,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"password": "password_required"}

    # Test successful login
    with patch("custom_components.toyota.config_flow.asyncio.run") as mock_run:
        mock_run.return_value = None  # Successful login
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_EMAIL: "test@example.com",
                CONF_PASSWORD: "password",
                CONF_METRIC_VALUES: True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "test@example.com"
    assert result["data"] == {
        CONF_EMAIL: "test@example.com",
        CONF_PASSWORD: "password",
        CONF_METRIC_VALUES: True,
    }

async def test_form_invalid_auth(hass, mock_toyota_client):
    """Test we handle invalid auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("custom_components.toyota.config_flow.asyncio.run") as mock_run:
        mock_run.side_effect = ToyotaLoginError("Invalid credentials")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_EMAIL: "test@example.com",
                CONF_PASSWORD: "wrong_password",
                CONF_METRIC_VALUES: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

async def test_form_toyota_exception(hass, mock_toyota_client):
    """Test we handle Toyota exception."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("custom_components.toyota.config_flow.asyncio.run") as mock_run:
        mock_run.side_effect = Exception("Toyota API error")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_EMAIL: "test@example.com",
                CONF_PASSWORD: "password",
                CONF_METRIC_VALUES: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}

async def test_form_duplicate_entries(hass, mock_toyota_client):
    """Test we handle duplicate entries."""
    # Setup an existing entry
    entry = config_entries.ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="test@example.com",
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True,
        },
        source=config_entries.SOURCE_USER,
        options={},
        entry_id="test_entry_id",
        state=config_entries.ConfigEntryState.LOADED,
    )
    hass.config_entries.async_entries = lambda domain: [entry] if domain == DOMAIN else []

    # Try to add the same entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("custom_components.toyota.config_flow.asyncio.run") as mock_run:
        mock_run.return_value = None  # Successful login
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_EMAIL: "test@example.com",
                CONF_PASSWORD: "password",
                CONF_METRIC_VALUES: True,
            },
        )

    # It should identify this as a duplicate
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"

async def test_reauth_flow(hass, mock_toyota_client):
    """Test the reauthentication flow."""
    # Setup an existing entry
    entry = config_entries.ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="test@example.com",
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "old_password",
            CONF_METRIC_VALUES: True,
        },
        source=config_entries.SOURCE_USER,
        options={},
        entry_id="test_entry_id",
        state=config_entries.ConfigEntryState.LOADED,
    )

    # Initialize a reauth flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Test successful reauth
    with patch("custom_components.toyota.config_flow.asyncio.run") as mock_run:
        mock_run.return_value = None  # Successful login
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_PASSWORD: "new_password",
            },
        )

    # Mock the entry so that async_update_entry works
    hass.config_entries._entries = {"test_entry_id": entry}
    hass.config_entries.async_update_entry = lambda entry, **kwargs: None

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    # Check that the password was updated
    assert entry.data[CONF_PASSWORD] == "new_password"
