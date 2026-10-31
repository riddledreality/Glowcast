"""mode=auto must pick the current holiday theme immediately (not only at midnight)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from custom_components.glowcast.const import DOMAIN, MODE_AUTO, MODE_MANUAL
from custom_components.glowcast.rooms import Room
from custom_components.glowcast.show import ShowManager
from custom_components.glowcast.themes import select_auto_theme

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


def test_oct_30_is_halloween():
    assert select_auto_theme(date(2026, 10, 30)).key == "halloween"


@pytest.mark.asyncio
async def test_ensure_room_applies_auto_theme_on_setup(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = Room(room_id="living", name="Living", kind="area", area_id="living")
    with patch(
        "custom_components.glowcast.show.select_auto_theme",
        return_value=select_auto_theme(date(2026, 10, 30)),
    ):
        show = manager.ensure_room(room)
    assert show.mode == MODE_AUTO
    assert show.theme_key == "halloween"
    assert show.effect == "flicker"  # halloween default


@pytest.mark.asyncio
async def test_apply_auto_theme_on_mode_switch(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = Room(room_id="den", name="Den", kind="custom", entity_ids=["light.a"])
    show = manager.ensure_room(room)
    show.mode = MODE_MANUAL
    show.theme_key = "calm"
    show.effect = "breathe"

    show.mode = MODE_AUTO
    with patch(
        "custom_components.glowcast.show.select_auto_theme",
        return_value=select_auto_theme(date(2026, 10, 30)),
    ):
        manager.apply_auto_theme(show)

    assert show.theme_key == "halloween"
    assert show.effect == "flicker"


@pytest.mark.asyncio
async def test_mode_select_switches_to_auto_updates_theme(hass: HomeAssistant):
    """GlowCastModeSelect must apply auto theme immediately when selecting auto."""
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    living = area_reg.async_create("Auto Room")
    light = ent_reg.async_get_or_create(
        "light", "test", "auto1", suggested_object_id="auto_lamp"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=living.id)
    hass.states.async_set(light.entity_id, "off", {"supported_color_modes": ["rgb"]})

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager: ShowManager = hass.data[DOMAIN]["manager"]
    show = manager.shows[living.id]
    show.mode = MODE_MANUAL
    show.theme_key = "calm"

    mode_entity_id = "select.glowcast_auto_room_mode"
    theme_entity_id = "select.glowcast_auto_room_theme"
    assert hass.states.get(mode_entity_id) is not None

    with patch(
        "custom_components.glowcast.show.select_auto_theme",
        return_value=select_auto_theme(date(2026, 10, 30)),
    ):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": mode_entity_id, "option": MODE_AUTO},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert show.mode == MODE_AUTO
    assert show.theme_key == "halloween"
    theme_state = hass.states.get(theme_entity_id)
    assert theme_state is not None
    assert theme_state.state == "halloween"


@pytest.mark.asyncio
async def test_setup_auto_theme_not_stuck_on_calm(hass: HomeAssistant):
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    area = area_reg.async_create("Oct Room")
    light = ent_reg.async_get_or_create(
        "light", "test", "oct1", suggested_object_id="oct_lamp"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=area.id)
    hass.states.async_set(light.entity_id, "off", {"supported_color_modes": ["rgb"]})

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)

    with patch(
        "custom_components.glowcast.show.select_auto_theme",
        return_value=select_auto_theme(date(2026, 10, 30)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    show = hass.data[DOMAIN]["manager"].shows[area.id]
    assert show.mode == MODE_AUTO
    assert show.theme_key == "halloween"
