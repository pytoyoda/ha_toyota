"""Fixtures for Toyota EU community integration tests."""

from unittest.mock import patch, Mock
import pytest

from custom_components.toyota.const import DOMAIN

@pytest.fixture(autouse=True)
def mock_integration(hass):
    """Mock the Toyota integration."""
    with patch("homeassistant.loader.async_get_integration") as mock_get:
        mock_integration = Mock()
        mock_integration.domain = DOMAIN
        # Simuliere einen config_flow
        mock_integration.get_component = lambda: Mock(CONFIG_SCHEMA=None)
        mock_get.return_value = mock_integration
        yield mock_integration

@pytest.fixture
def mock_toyota_client():
    """Return a mocked Toyota client."""
    with patch("custom_components.toyota.config_flow.MyT", autospec=True) as mock_client_class:
        client = mock_client_class.return_value
        # Set up necessary mocked functions
        client.login = lambda: None  # Will be mocked in tests
        yield client
