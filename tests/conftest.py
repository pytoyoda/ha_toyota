"""Fixtures for Toyota EU community integration tests."""

from unittest.mock import patch
import pytest

@pytest.fixture
def mock_toyota_client():
    """Return a mocked Toyota client."""
    with patch("custom_components.toyota.config_flow.MyT", autospec=True) as mock_client_class:
        client = mock_client_class.return_value
        # Set up necessary mocked functions
        client.login = lambda: None  # Will be mocked in tests
        yield client
