"""Room auto-population from areas."""

from __future__ import annotations

import pytest

from custom_components.glowcast.const import DOMAIN, ROOM_ALL
from custom_components.glowcast.rooms import build_rooms

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.asyncio
async def test_rooms_from_areas_with_lights(hass: HomeAssistant):
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)

    living = area_reg.async_create("Living Room")
    kitchen = area_reg.async_create("Kitchen")
    area_reg.async_create("Empty Closet")

    living_entry = ent_reg.async_get_or_create(
        "light", "test", "living1", suggested_object_id="living_lamp"
    )
    kitchen_entry = ent_reg.async_get_or_create(
        "light", "test", "kitchen1", suggested_object_id="kitchen_spot"
    )
    orphan_entry = ent_reg.async_get_or_create(
        "light", "test", "orphan1", suggested_object_id="orphan"
    )

    ent_reg.async_update_entity(living_entry.entity_id, area_id=living.id)
    ent_reg.async_update_entity(kitchen_entry.entity_id, area_id=kitchen.id)

    for eid in (living_entry.entity_id, kitchen_entry.entity_id, orphan_entry.entity_id):
        hass.states.async_set(
            eid,
            "off",
            {"supported_color_modes": ["rgb"], "supported_features": 0},
        )

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)

    rooms = build_rooms(hass)
    assert ROOM_ALL in rooms
    assert living.id in rooms
    assert kitchen.id in rooms
    assert rooms[living.id].name == "Living Room"
    assert len([r for r in rooms.values() if r.kind == "area"]) == 2

    living_lights = rooms[living.id].resolve_lights(hass)
    assert {li.entity_id for li in living_lights} == {living_entry.entity_id}

    all_lights = rooms[ROOM_ALL].resolve_lights(hass)
    assert {li.entity_id for li in all_lights} >= {
        living_entry.entity_id,
        kitchen_entry.entity_id,
        orphan_entry.entity_id,
    }


@pytest.mark.asyncio
async def test_excluded_lights_omitted(hass: HomeAssistant):
    hass.states.async_set("light.keep", "on", {"supported_color_modes": ["rgb"]})
    hass.states.async_set("light.skip", "on", {"supported_color_modes": ["rgb"]})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={"excluded_lights": ["light.skip"]},
    )
    entry.add_to_hass(hass)

    rooms = build_rooms(hass)
    ids = {li.entity_id for li in rooms[ROOM_ALL].resolve_lights(hass)}
    assert "light.keep" in ids
    assert "light.skip" not in ids


@pytest.mark.asyncio
async def test_custom_group(hass: HomeAssistant):
    hass.states.async_set("light.a", "on", {"supported_color_modes": ["rgb"]})
    hass.states.async_set("light.b", "on", {"supported_color_modes": ["rgb"]})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "custom_groups": [
                {
                    "id": "party_zone",
                    "name": "Party Zone",
                    "lights": ["light.a", "light.b"],
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    rooms = build_rooms(hass)
    assert "party_zone" in rooms
    assert rooms["party_zone"].kind == "custom"
    ids = {li.entity_id for li in rooms["party_zone"].resolve_lights(hass)}
    assert ids == {"light.a", "light.b"}
