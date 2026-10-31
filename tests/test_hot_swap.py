"""Hot-swap theme/effect into a running show without stop/restore."""

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

from homeassistant.core import HomeAssistant, ServiceRegistry


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
async def test_hot_swap_theme_while_running_no_restore(hass: HomeAssistant):
    """Changing theme on a running show updates palette immediately without restore."""
    manager = ShowManager(hass)
    room = _room()
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", slow=False, min_interval=0.01)

    original_saved = _saved_off()
    show._saved = dict(original_saved)
    show.running = True
    show.mode = MODE_MANUAL
    show.theme_key = "calm"
    show.effect = THEMES["calm"].default_effect

    frames: list[str] = []
    first_frame = asyncio.Event()

    async def capture_animate(s):
        # Record which theme the frame would use
        theme = manager.current_theme(s)
        frames.append(theme.key)
        first_frame.set()
        # Simulate a long sleep that hot-swap must wake
        try:
            await asyncio.wait_for(s._wake.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        s._wake.clear()

    show._task = hass.async_create_task(manager._show_loop(show))

    restore_calls: list = []

    async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        if service in ("turn_off", "turn_on") and blocking:
            restore_calls.append((service, dict(service_data or {}), blocking))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", side_effect=capture_animate),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
        patch.object(manager, "_settle_before_restore", new_callable=AsyncMock),
    ):
        # Kick loop into first frame
        await asyncio.wait_for(first_frame.wait(), timeout=2)
        assert frames[0] == "calm"
        saved_before = dict(show._saved)

        # Hot-swap via start() (service path) — must not stop/restore
        first_frame.clear()
        await manager.start(room.room_id, "halloween")
        await asyncio.wait_for(first_frame.wait(), timeout=2)

        assert show.running is True
        assert show.theme_key == "halloween"
        assert show.mode == MODE_MANUAL
        assert show._saved == saved_before
        assert "halloween" in frames
        restore_mock.assert_not_awaited()
        assert restore_calls == []

        # Cleanup
        show.running = False
        show._wake.set()
        show._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await show._task


@pytest.mark.asyncio
async def test_rapid_theme_switches_last_wins(hass: HomeAssistant):
    """Rapid consecutive theme changes coalesce to the latest value (no queue)."""
    manager = ShowManager(hass)
    room = _room("party")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.01)

    show._saved = _saved_off()
    show.running = True
    show.mode = MODE_MANUAL
    show.theme_key = "calm"

    seen: list[str] = []
    gate = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_animate(s):
        seen.append(s.theme_key)
        gate.set()
        await proceed.wait()
        proceed.clear()
        # After proceeding, honor wake immediately if set
        if s._wake.is_set():
            s._wake.clear()

    show._task = hass.async_create_task(manager._show_loop(show))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", side_effect=slow_animate),
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
    ):
        await asyncio.wait_for(gate.wait(), timeout=2)
        assert seen[0] == "calm"

        # Fire three rapid start_theme / hot-swaps while first frame is held
        await manager.start(room.room_id, "aurora")
        await manager.start(room.room_id, "ocean")
        await manager.start(room.room_id, "halloween")
        assert show.theme_key == "halloween"  # last write wins immediately

        gate.clear()
        proceed.set()  # release first frame sleep path via loop
        # Wake so loop continues promptly
        show._wake.set()
        await asyncio.wait_for(gate.wait(), timeout=2)

        assert show.theme_key == "halloween"
        assert seen[-1] == "halloween"
        # Never queued through aurora/ocean as separate restore cycles
        restore_mock.assert_not_awaited()
        assert show.running is True
        assert show._saved == _saved_off()

        show.running = False
        show._wake.set()
        proceed.set()
        show._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await show._task


@pytest.mark.asyncio
async def test_select_hot_swap_writes_state_before_wake(hass: HomeAssistant):
    """Theme select updates HA state instantly and wakes without calling start/stop."""
    manager = ShowManager(hass)
    room = _room("ui")
    show = manager.ensure_room(room)
    show.running = True
    show.mode = MODE_MANUAL
    show.theme_key = "calm"
    show._saved = _saved_off()
    # Fake an active loop task so start() would take hot-swap path if called
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
    # write_state must precede wake
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
    """start_theme on a running show must not re-snapshot (would poison restore)."""
    manager = ShowManager(hass)
    room = _room("snap")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.01)

    show.running = True
    show.mode = MODE_MANUAL
    show.theme_key = "calm"
    show._saved = _saved_off()
    show._task = hass.async_create_task(asyncio.sleep(60))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
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
    assert show._wake.is_set()

    show._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await show._task


@pytest.mark.asyncio
async def test_stop_still_restores_after_hot_swaps(hass: HomeAssistant):
    """Stop/restore behavior from 0.1.1 remains correct after hot-swaps."""
    manager = ShowManager(hass)
    room = _room("end")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", slow=True, min_interval=1.0)
    light.mark_updated()

    show.running = True
    show._saved = _saved_off()
    show.theme_key = "halloween"

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_loop(_show):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    show._task = hass.async_create_task(long_loop(show))
    await started.wait()

    # Simulate hot-swaps
    show.theme_key = "aurora"
    show._wake.set()
    show.theme_key = "ocean"
    show._wake.set()

    calls: list[tuple] = []

    async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        calls.append((domain, service, dict(service_data or {}), blocking))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
        patch("custom_components.glowcast.show.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
    ):
        await manager.stop(room.room_id)

    assert cancelled.is_set()
    assert show.running is False
    assert show._task is None
    assert sleep_mock.await_count >= 1
    assert len(calls) == 1
    assert calls[0][1] == "turn_off"
    assert calls[0][3] is True
    assert show._saved == {}
