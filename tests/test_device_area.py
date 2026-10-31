"""Lights inherit area from their parent device when the entity has no area."""

from __future__ import annotations

import pytest

from custom_components.glowcast.lights import collect_lights, lights_for_area

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.asyncio
async def test_light_uses_device_area(hass: HomeAssistant):
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    den = area_reg.async_create("Den")
    # Config entry required for device registry in some HA versions
    entry = MockConfigEntry(domain="test", data={})
    entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "bulb1")},
        name="Bulb",
    )
    dev_reg.async_update_device(device.id, area_id=den.id)

    light = ent_reg.async_get_or_create(
        "light", "test", "bulb1", suggested_object_id="device_area_lamp", device_id=device.id
    )
    # Entity itself has no area_id — should fall back to device area
    assert light.area_id is None
    hass.states.async_set(light.entity_id, "off", {"supported_color_modes": ["rgb"]})

    found = lights_for_area(hass, den.id)
    assert any(li.entity_id == light.entity_id for li in found)

    all_lights = collect_lights(hass)
    match = next(li for li in all_lights if li.entity_id == light.entity_id)
    assert match.area_id == den.id
