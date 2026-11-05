"""Show stop must settle and restore without racing in-flight color updates."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.glowcast.lights import LightInfo
from custom_components.glowcast.rooms import Room
from custom_components.glowcast.show import ShowManager

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant, ServiceRegistry


def _room(room_id: str = "den") -> Room:
    return Room(room_id=room_id, name="Den", kind="custom", entity_ids=["light.den"])


@pytest.mark.asyncio
async def test_stop_cancels_task_then_restores_with_timeout(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room()
    show = manager.ensure_room(room)

    light = LightInfo("light.den", None, "color", slow=True, min_interval=1.0)
    manager.rate.last_sent["light.den"] = time.monotonic()

    show.running = True
    show._saved = {
        "light.den": {
            "state": "off",
            "brightness": None,
            "rgb_color": None,
            "hs_color": None,
            "color_temp_kelvin": None,
        }
    }
    hass.states.async_set("light.den", "on", {"supported_color_modes": ["rgb"]})

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_animate(_show):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    show._task = hass.async_create_task(long_animate(show))
    await started.wait()

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
    settle_arg = sleep_mock.await_args_list[0].args[0]
    assert settle_arg == pytest.approx(1.0, abs=0.05)

    assert len(calls) == 1
    domain, service, data, blocking = calls[0]
    assert domain == "light"
    assert service == "turn_off"
    assert data["entity_id"] == "light.den"
    assert blocking is True


@pytest.mark.asyncio
async def test_restore_on_uses_blocking_under_wait_for(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("kitchen")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", slow=False, min_interval=0.5)
    show._saved = {
        "light.den": {
            "state": "on",
            "brightness": 128,
            "rgb_color": [255, 0, 0],
            "hs_color": None,
            "color_temp_kelvin": None,
        }
    }
    hass.states.async_set("light.den", "on", {"brightness": 50})

    calls: list[tuple] = []

    async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        calls.append((domain, service, dict(service_data or {}), blocking))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
        patch("custom_components.glowcast.show.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
    ):
        await manager.stop(room.room_id)

    # No recent last_sent → no settle sleep
    assert len(calls) == 1
    domain, service, data, blocking = calls[0]
    assert service == "turn_on"
    assert data["brightness"] == 128
    assert data["rgb_color"] == [255, 0, 0]
    assert blocking is True


@pytest.mark.asyncio
async def test_settle_uses_persisted_fast_interval(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("fast")
    show = manager.ensure_room(room)
    light = LightInfo("light.fast", None, "color", slow=False, min_interval=0.5)
    manager.rate.last_sent["light.fast"] = time.monotonic()
    show._saved = {"light.fast": {"state": "off"}}
    hass.states.async_set("light.fast", "on")

    async def noop_call(self, *args, **kwargs):
        return None

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=noop_call),
        patch("custom_components.glowcast.show.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
    ):
        await manager.stop(room.room_id)

    assert sleep_mock.await_count == 1
    assert sleep_mock.await_args.args[0] == pytest.approx(0.5, abs=0.05)


@pytest.mark.asyncio
async def test_animate_skips_when_not_running(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("lock")
    show = manager.ensure_room(room)
    show.running = False
    light = LightInfo("light.den", None, "color", False, 0.5)

    calls: list = []

    async def capture_call(self, *args, **kwargs):
        calls.append(args)

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
    ):
        await manager._animate_room(show)

    assert calls == []


@pytest.mark.asyncio
async def test_unavailable_light_during_restore_does_not_hang(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("hang")
    show = manager.ensure_room(room)
    show._saved = {
        "light.gone": {"state": "off"},
        "light.slowpoke": {
            "state": "on",
            "brightness": 100,
            "rgb_color": [1, 2, 3],
            "hs_color": None,
            "color_temp_kelvin": None,
        },
    }
    hass.states.async_set("light.gone", "unavailable")
    hass.states.async_set("light.slowpoke", "on", {"brightness": 10})

    started = asyncio.Event()

    async def hung_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        eid = (service_data or {}).get("entity_id")
        if eid == "light.slowpoke":
            started.set()
            await asyncio.sleep(60)
        return None

    with (
        patch.object(ServiceRegistry, "async_call", new=hung_call),
        patch("custom_components.glowcast.show.RESTORE_LIGHT_TIMEOUT", 0.05),
    ):
        await asyncio.wait_for(manager.stop(room.room_id), timeout=2.0)

    assert show.running is False
    assert show._saved == {}
    assert started.is_set()


@pytest.mark.asyncio
async def test_stop_during_transition_cancels_promptly(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("mid")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    show.running = True
    show._saved = {"light.den": {"state": "off"}}
    hass.states.async_set("light.den", "on")

    gate = asyncio.Event()

    async def stuck_frame(_show):
        gate.set()
        await asyncio.sleep(60)

    show._task = hass.async_create_task(manager._show_loop(show))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", side_effect=stuck_frame),
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
        patch.object(manager, "_settle_before_restore", new_callable=AsyncMock),
    ):
        await asyncio.wait_for(gate.wait(), timeout=2)
        await asyncio.wait_for(manager.stop(room.room_id), timeout=2)

    assert show.running is False
    assert show._task is None
    restore_mock.assert_awaited()


@pytest.mark.asyncio
async def test_theme_swap_preserves_preshow_snapshot(hass: HomeAssistant):
    """start_theme while running must not overwrite the pre-show snapshot."""
    manager = ShowManager(hass)
    room = _room("swap")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", False, 0.5)
    hass.states.async_set("light.den", "off")

    original = {
        "light.den": {
            "state": "off",
            "brightness": None,
            "rgb_color": None,
            "hs_color": None,
            "color_temp_kelvin": None,
        }
    }

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch(
            "custom_components.glowcast.show.snapshot_states",
            return_value={"light.den": {"state": "on", "brightness": 99}},
        ) as snap,
    ):
        show._saved = dict(original)
        show.running = True
        show._task = hass.async_create_task(asyncio.sleep(60))

        await manager.start(room.room_id, "halloween")
        snap.assert_not_called()
        assert show._saved == original
        assert show.theme_key == "halloween"
        assert show.running is True
        assert manager._active_task_count() == 1

        await manager.stop(room.room_id, restore=False)
