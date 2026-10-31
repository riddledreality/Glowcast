"""Regression tests for stop/restore crashes and task stacking (v0.1.5)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.glowcast.const import DEFAULT_FAST_MIN_INTERVAL, DOMAIN
from custom_components.glowcast.lights import LightInfo
from custom_components.glowcast.rooms import Room
from custom_components.glowcast.show import ShowManager

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant


def _room(room_id: str = "den", eid: str = "light.den") -> Room:
    return Room(room_id=room_id, name=room_id.title(), kind="custom", entity_ids=[eid])


@pytest.mark.asyncio
async def test_rapid_theme_swaps_do_not_stack_tasks(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("party")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    hass.states.async_set("light.den", "on", {"supported_color_modes": ["rgb"]})

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
    ):
        await manager.start(room.room_id, "calm")
        assert show._task is not None and not show._task.done()

        for theme in ("aurora", "ocean", "halloween", "party", "sunset"):
            await manager.start(room.room_id, theme)

        assert show.theme_key == "sunset"
        assert manager._active_task_count() == 1
        assert show._saved  # snapshot preserved across swaps

        await manager.stop(room.room_id)
        assert show._task is None
        assert manager._active_task_count() == 0


@pytest.mark.asyncio
async def test_spam_start_stop_single_task(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("spam")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    hass.states.async_set("light.den", "off")

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch.object(manager, "_restore", new_callable=AsyncMock),
        patch.object(manager, "_settle_before_restore", new_callable=AsyncMock),
    ):
        for i in range(20):
            await manager.start(room.room_id, "halloween" if i % 2 == 0 else "calm")
            if i % 3 == 0:
                await manager.stop(room.room_id)
        assert manager._active_task_count() <= 1
        await manager.stop(room.room_id)
        assert manager._active_task_count() == 0


@pytest.mark.asyncio
async def test_unload_cancels_all_tasks(hass: HomeAssistant):
    manager = ShowManager(hass)
    rooms = [_room("a", "light.a"), _room("b", "light.b")]
    tasks = []
    for room in rooms:
        show = manager.ensure_room(room)
        show.running = True
        show._saved = {room.entity_ids[0]: {"state": "off"}}
        hass.states.async_set(room.entity_ids[0], "on")

        async def sleeper():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise

        show._task = hass.async_create_task(sleeper())
        tasks.append(show._task)

    with patch.object(manager, "_restore", new_callable=AsyncMock):
        await asyncio.wait_for(manager.async_unload(), timeout=2.0)

    assert all(t.done() or t.cancelled() for t in tasks)
    assert all(s._task is None for s in manager.shows.values())
    assert all(s.running is False for s in manager.shows.values())


@pytest.mark.asyncio
async def test_disabled_refuses_start_and_emergency_skips_restore(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room()
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    hass.states.async_set("light.den", "off")

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
    ):
        await manager.start(room.room_id, "halloween")
        assert show.running is True
        assert show._saved

        await manager.emergency_stop(room.room_id)
        assert show.running is False
        assert show._task is None
        assert show._saved == {}
        restore_mock.assert_not_awaited()

        manager.enabled = False
        await manager.start(room.room_id, "calm")
        assert show.running is False


@pytest.mark.asyncio
async def test_user_turns_off_light_stops_show(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room()
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    hass.states.async_set("light.den", "on", {"supported_color_modes": ["rgb"]})

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch.object(manager, "_restore", new_callable=AsyncMock),
        patch.object(manager, "_settle_before_restore", new_callable=AsyncMock),
    ):
        await manager.start(room.room_id, "calm")
        show._commanded_on.add("light.den")
        # Simulate user turning the light off
        hass.states.async_set("light.den", "off")
        await hass.async_block_till_done()
        # Give the created stop task a moment
        for _ in range(20):
            if not show.running:
                break
            await asyncio.sleep(0.05)
        assert show.running is False


@pytest.mark.asyncio
async def test_stop_all_service_and_enabled_switch(hass: HomeAssistant):
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    living = area_reg.async_create("Den")
    light = ent_reg.async_get_or_create(
        "light", "test", "den1", suggested_object_id="den_lamp"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=living.id)
    hass.states.async_set(light.entity_id, "off", {"supported_color_modes": ["rgb"]})

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "stop_all")
    assert hass.services.has_service(DOMAIN, "stop")

    enabled = hass.states.get("switch.glowcast_enabled")
    if enabled is None:
        enabled_list = [s for s in hass.states.async_all("switch") if "enabled" in s.entity_id]
        assert enabled_list, "expected switch.glowcast_enabled"
        enabled = enabled_list[0]
    assert enabled.state == "on"

    await hass.services.async_call(
        DOMAIN, "start_theme", {"room": living.id, "theme": "halloween"}, blocking=True
    )
    await hass.async_block_till_done()
    show = hass.data[DOMAIN]["manager"].shows[living.id]
    assert show.running is True

    await hass.services.async_call(DOMAIN, "stop_all", {}, blocking=True)
    await hass.async_block_till_done()
    assert show.running is False

    assert await hass.config_entries.async_unload(entry.entry_id)


def test_default_fast_interval():
    assert DEFAULT_FAST_MIN_INTERVAL >= 0.5
