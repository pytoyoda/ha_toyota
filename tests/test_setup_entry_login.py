"""Tests for error handling of the initial login in async_setup_entry."""

from __future__ import annotations

import httpcore
import httpx
import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytoyoda.exceptions import ToyotaLoginError

from custom_components.toyota.const import CONF_METRIC_VALUES, DOMAIN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("login_error", "expected_state"),
    [
        (httpx.ConnectError("[Errno -3] Try again"), ConfigEntryState.SETUP_RETRY),
        (httpcore.ConnectError("[Errno -3] Try again"), ConfigEntryState.SETUP_RETRY),
        (httpx.ConnectTimeout("timed out"), ConfigEntryState.SETUP_RETRY),
        (ToyotaLoginError("Authentication Failed."), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_login_error_sets_entry_state(
    hass, monkeypatch, login_error, expected_state
):
    """Network errors at login must retry setup; bad credentials must not."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "email": "test@example.com",
            "password": "password",
            CONF_METRIC_VALUES: True,
        },
        entry_id="entry1",
        title="Toyota test",
    )
    entry.add_to_hass(hass)

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def login(self) -> None:
            raise login_error

    monkeypatch.setattr("custom_components.toyota.MyT", _FakeClient)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is expected_state
