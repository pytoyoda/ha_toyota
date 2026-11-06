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
        (httpx.ReadTimeout("timed out"), ConfigEntryState.SETUP_RETRY),
        (httpx.ReadError("connection reset"), ConfigEntryState.SETUP_RETRY),
        (httpx.RemoteProtocolError("disconnected"), ConfigEntryState.SETUP_RETRY),
        (httpx.PoolTimeout("timed out"), ConfigEntryState.SETUP_RETRY),
        (httpcore.ReadTimeout("timed out"), ConfigEntryState.SETUP_RETRY),
        (ToyotaLoginError("Authentication Failed."), ConfigEntryState.SETUP_ERROR),
        # Toyota's login gateway down (seen as nginx 502 / CloudFront 504):
        # the credentials were never judged, so setup must retry, not reauth.
        (
            ToyotaLoginError("Authentication Failed. 502, <html>Bad Gateway</html>."),
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            ToyotaLoginError("Authorization failed. 503, Service Unavailable."),
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            ToyotaLoginError("Token retrieval failed. 504, Gateway Timeout."),
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            ToyotaLoginError("Token refresh failed. 429, Too Many Requests."),
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            ToyotaLoginError("Authentication Failed. 401, {}."),
            ConfigEntryState.SETUP_ERROR,
        ),
        (
            ToyotaLoginError("Token retrieval failed. 400, invalid_grant."),
            ConfigEntryState.SETUP_ERROR,
        ),
    ],
)
async def test_login_error_sets_entry_state(
    hass, monkeypatch, login_error, expected_state
):
    """Network and server errors at login must retry setup; bad credentials not."""
    entry = _add_entry(hass)
    _fake_login(monkeypatch, login_error)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is expected_state
    reauth_started = any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )
    assert reauth_started is (expected_state is ConfigEntryState.SETUP_ERROR)


class _StatusLoginError(ToyotaLoginError):
    """Mimics pytoyoda's ToyotaLoginServerError (status carried as attribute)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_login_server_error_attribute_retries_setup(hass, monkeypatch):
    """A login error exposing a 5xx status_code retries setup.

    pytoyoda releases with ToyotaLoginServerError expose the HTTP status as an
    attribute; the integration must honor it without parsing the message.
    """
    entry = _add_entry(hass)
    _fake_login(monkeypatch, _StatusLoginError("Login service unavailable", 502))

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_pytoyoda_login_server_error_retries_setup(hass, monkeypatch):
    """pytoyoda's own ToyotaLoginServerError retries setup when available."""
    exceptions = pytest.importorskip("pytoyoda.exceptions")
    server_error = getattr(exceptions, "ToyotaLoginServerError", None)
    if server_error is None:
        pytest.skip("installed pytoyoda predates ToyotaLoginServerError")
    entry = _add_entry(hass)
    _fake_login(
        monkeypatch,
        server_error("Authentication Failed. 504, timeout.", status_code=504),
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


def _add_entry(hass) -> MockConfigEntry:
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
    return entry


def _fake_login(monkeypatch, login_error: BaseException) -> None:
    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def login(self) -> None:
            raise login_error

    monkeypatch.setattr("custom_components.toyota.MyT", _FakeClient)
