"""Config flow tests."""

from __future__ import annotations

import pytest

from custom_components.glowcast.const import DOMAIN

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.asyncio
async def test_user_flow_creates_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    await hass.async_block_till_done()
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "GlowCast"
    assert result2["data"] == {}


@pytest.mark.asyncio
async def test_single_instance(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="GlowCast")
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.asyncio
async def test_options_flow_general(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="GlowCast", options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "general"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "general"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "excluded_lights": [],
            "slow_lights": [],
            "slow_min_interval": 1.0,
            "fast_min_interval": 0.5,
            "team_colors": [[255, 0, 0], [0, 0, 255]],
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["slow_min_interval"] == 1.0
    assert entry.options["team_colors"][0] == [255, 0, 0]


@pytest.mark.asyncio
async def test_options_custom_theme(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="GlowCast", options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "custom_theme"}
    )
    assert result["step_id"] == "custom_theme"
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "name": "Neon Nights",
            "colors": [[255, 0, 255], [0, 255, 255], [255, 255, 0]],
            "effect": "twinkle",
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    themes = entry.options["custom_themes"]
    assert themes[0]["key"] == "neon_nights"
    assert themes[0]["effect"] == "twinkle"
