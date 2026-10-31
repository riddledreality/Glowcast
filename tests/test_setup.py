"""Integration setup smoke test."""

from __future__ import annotations

import pytest

from custom_components.glowcast.const import DOMAIN, SERVICE_START_THEME, SERVICE_STOP

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.asyncio
async def test_setup_creates_manager_and_services(hass: HomeAssistant):
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    living = area_reg.async_create("Den")
    light = ent_reg.async_get_or_create(
        "light", "test", "den1", suggested_object_id="den_lamp"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=living.id)
    hass.states.async_set(
        light.entity_id, "off", {"supported_color_modes": ["rgb"]}
    )

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert DOMAIN in hass.data
    assert hass.data[DOMAIN]["manager"] is not None
    assert hass.services.has_service(DOMAIN, SERVICE_START_THEME)
    assert hass.services.has_service(DOMAIN, SERVICE_STOP)
    rooms = hass.data[DOMAIN]["rooms"]
    assert "all_lights" in rooms
    assert living.id in rooms

    await hass.services.async_call(
        DOMAIN,
        SERVICE_START_THEME,
        {"room": living.id, "theme": "halloween"},
        blocking=True,
    )
    await hass.async_block_till_done()
    show = hass.data[DOMAIN]["manager"].shows[living.id]
    assert show.running is True
    assert show.theme_key == "halloween"

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {"room": living.id}, blocking=True
    )
    await hass.async_block_till_done()
    assert show.running is False

    assert await hass.config_entries.async_unload(entry.entry_id)
