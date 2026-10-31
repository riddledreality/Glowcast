"""Theme changes while running: one task, snapshot preserved, select wakes live loop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.glowcast.const import MODE_MANUAL
from custom_components.glowcast.lights import LightInfo
from custom_components.glowcast.rooms import Room
from custom_components.glowcast.select import GlowCastThemeSelect
from custom_components.glowcast.show import ShowManager
from custom_components.glowcast.themes import THEMES

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant


def _room(room_id: str = "den") -> Room:
    return Room(room_id=room_id, name="Den", kind="custom", entity_ids=["light.den"])


def _saved_off() -> dict:
    return {
        "light.den": {
            "state": "off",
            "brightness": None,
            "rgb_color": None,
            "hs_color": None,
            "color_temp_kelvin": None,
        }
    }


@pytest.mark.asyncio
async def test_start_theme_swap_preserves_snapshot_one_task(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room()
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", slow=False, min_interval=0.5)
    hass.states.async_set("light.den", "off")

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
    ):
        await manager.start(room.room_id, "calm")
        saved = dict(show._saved)
        first_task = show._task
        assert first_task is not None

        await manager.start(room.room_id, "halloween")
        assert show.theme_key == "halloween"
        assert show._saved == saved
        assert manager._active_task_count() == 1
        restore_mock.assert_not_awaited()
        # Old task cancelled; new task running
        assert show._task is not None and show._task is not first_task

        await manager.stop(room.room_id, restore=False)


@pytest.mark.asyncio
async def test_rapid_theme_switches_last_wins(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("party")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    hass.states.async_set("light.den", "off")

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
    ):
        await manager.start(room.room_id, "calm")
        await manager.start(room.room_id, "aurora")
        await manager.start(room.room_id, "ocean")
        await manager.start(room.room_id, "halloween")
        assert show.theme_key == "halloween"
        assert manager._active_task_count() == 1
        restore_mock.assert_not_awaited()
        await manager.stop(room.room_id, restore=False)


@pytest.mark.asyncio
async def test_select_hot_swap_writes_state_before_wake(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("ui")
    show = manager.ensure_room(room)
    show.running = True
    show.mode = MODE_MANUAL
    show.theme_key = "calm"
    show._saved = _saved_off()
    show._task = hass.async_create_task(asyncio.sleep(60))

    class _Entry:
        options: dict = {}

    entry = _Entry()
    entity = GlowCastThemeSelect(manager, room, entry)
    entity.hass = hass
    entity.entity_id = "select.glowcast_ui_theme"

    order: list[str] = []

    def write_state():
        order.append("write_state")
        order.append(f"theme={show.theme_key}")

    def wake(rid):
        order.append(f"wake:{rid}")
        show._wake.set()

    with (
        patch.object(entity, "async_write_ha_state", side_effect=write_state),
        patch.object(manager, "wake_show", side_effect=wake),
        patch.object(manager, "start", new_callable=AsyncMock) as start_mock,
        patch.object(manager, "stop", new_callable=AsyncMock) as stop_mock,
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
    ):
        await entity.async_select_option("halloween")

    assert show.theme_key == "halloween"
    assert show.mode == MODE_MANUAL
    assert show.effect == THEMES["halloween"].default_effect
    assert order[0] == "write_state"
    assert "theme=halloween" in order
    assert any(x.startswith("wake:") for x in order)
    assert order.index("write_state") < next(
        i for i, x in enumerate(order) if x.startswith("wake:")
    )
    start_mock.assert_not_awaited()
    stop_mock.assert_not_awaited()
    restore_mock.assert_not_awaited()

    show._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await show._task


@pytest.mark.asyncio
async def test_start_while_running_preserves_saved_snapshot(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("snap")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    hass.states.async_set("light.den", "on")

    show.running = True
    show.mode = MODE_MANUAL
    show.theme_key = "calm"
    show._saved = _saved_off()
    show._task = hass.async_create_task(asyncio.sleep(60))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch(
            "custom_components.glowcast.show.snapshot_states",
            return_value={"light.den": {"state": "on", "brightness": 99}},
        ) as snap,
    ):
        await manager.start(room.room_id, "party")

    snap.assert_not_called()
    assert show._saved == _saved_off()
    assert show.theme_key == "party"
    assert show.running is True
    await manager.stop(room.room_id, restore=False)


@pytest.mark.asyncio
async def test_stop_still_restores_after_hot_swaps(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("end")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", slow=True, min_interval=1.0)
    hass.states.async_set("light.den", "off")

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
    ):
        await manager.start(room.room_id, "halloween")
        await manager.start(room.room_id, "aurora")
        await manager.start(room.room_id, "ocean")
        assert show._saved == {
            "light.den": {
                "state": "off",
                "brightness": None,
                "rgb_color": None,
                "hs_color": None,
                "color_temp_kelvin": None,
                "color_mode": None,
            }
        } or show._saved["light.den"]["state"] == "off"

        calls: list[tuple] = []

        async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
            calls.append((domain, service, dict(service_data or {}), blocking))

        from homeassistant.core import ServiceRegistry

        with (
            patch.object(ServiceRegistry, "async_call", new=capture_call),
            patch("custom_components.glowcast.show.asyncio.sleep", new_callable=AsyncMock),
        ):
            await manager.stop(room.room_id)

        assert show.running is False
        assert show._task is None
        assert any(c[1] == "turn_off" for c in calls)
        assert show._saved == {}
