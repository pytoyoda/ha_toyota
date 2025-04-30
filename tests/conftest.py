"""Fixtures for Toyota EU community integration tests."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from custom_components.toyota.const import CONF_METRIC_VALUES, DOMAIN

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def init_integration(hass, aioclient_mock) -> MockConfigEntry:
    #mock_data = load_json_value_fixture("rest_response.json")
    #url = f"{FERNPORTAL_URL}/{MOCK_SERIAL_NUMBER}"
    #aioclient_mock.get(url, json=mock_data)

    # Create a mock config entry
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "password",
            CONF_METRIC_VALUES: True,
        },
        entry_id="test_entry_toyota",
        title="test_entry_toyota config",
        source="user",
    )
    entry.add_to_hass(hass)

    # Call async_setup_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry
