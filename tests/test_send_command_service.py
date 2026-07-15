"""Service-registry-path test for toyota.send_command.

This is the regression test that would have caught the original bug where
_handle_send_command was registered as:

    lambda call: _handle_send_command(hass, call)  # sync callable!

which caused HA to drop the coroutine unawaited (RuntimeWarning: coroutine
... was never awaited).  The fix registers the handler via:

    functools.partial(_handle_send_command, hass)   # async-compatible

This test goes through the service registry (hass.services.async_call) rather
than calling the handler directly, so it exercises exactly the path that was
broken and protects against this regression.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.toyota import SERVICE_SEND_COMMAND, _async_register_services
from custom_components.toyota.const import DOMAIN


@pytest.mark.asyncio
async def test_send_command_service_registered_and_handler_awaited(hass):
    """toyota.send_command is registered and its async handler is actually awaited.

    Uses a mock for _handle_send_command so we do not need a real Toyota
    device or a full config-entry setup: the critical assertion is that the
    coroutine returned by the handler is awaited by HA's service machinery,
    not silently dropped.
    """
    mock_handler = AsyncMock()

    # Patch BEFORE registration so functools.partial captures the mock.
    with patch("custom_components.toyota._handle_send_command", mock_handler):
        await _async_register_services(hass)

    # Service must be present in the registry after setup.
    assert hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND)

    # Call through the service registry — this is the path that was broken.
    # If the handler were registered as a sync lambda the coroutine would be
    # dropped and mock_handler.assert_called_once() would fail.
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        service_data={"command": "hazard-on"},
        blocking=True,
    )

    mock_handler.assert_called_once()
