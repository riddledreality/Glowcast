"""Show stop must settle and restore without racing in-flight color updates."""

from __future__ import annotations

import asyncio
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
async def test_stop_awaits_task_then_restores_blocking(hass: HomeAssistant):
    """Cancel in-flight animate, settle, restore with blocking=True; off stays off."""
    manager = ShowManager(hass)
    room = _room()
    show = manager.ensure_room(room)

    light = LightInfo("light.den", None, "color", slow=True, min_interval=1.0)
    light.mark_updated()  # just updated — settle should wait ~1s

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

    calls: list[tuple[str, str, dict, bool]] = []

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
    # Settled for Bluetooth min interval (~1.0s)
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
async def test_restore_on_uses_blocking_true(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("kitchen")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, "color", slow=False, min_interval=0.3)
    show._saved = {
        "light.den": {
            "state": "on",
            "brightness": 128,
            "rgb_color": [255, 0, 0],
            "hs_color": None,
            "color_temp_kelvin": None,
        }
    }

    calls: list[tuple] = []

    async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        calls.append((domain, service, dict(service_data or {}), blocking))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
        patch("custom_components.glowcast.show.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
    ):
        await manager.stop(room.room_id)

    sleep_mock.assert_not_awaited()
    assert len(calls) == 1
    domain, service, data, blocking = calls[0]
    assert service == "turn_on"
    assert data["brightness"] == 128
    assert data["rgb_color"] == [255, 0, 0]
    assert blocking is True


@pytest.mark.asyncio
async def test_settle_uses_fast_interval_for_non_ble(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("fast")
    show = manager.ensure_room(room)
    light = LightInfo("light.fast", None, "color", slow=False, min_interval=0.3)
    light.mark_updated()
    show._saved = {"light.fast": {"state": "off"}}

    async def noop_call(self, *args, **kwargs):
        return None

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=noop_call),
        patch("custom_components.glowcast.show.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
    ):
        await manager.stop(room.room_id)

    assert sleep_mock.await_count == 1
    assert sleep_mock.await_args.args[0] == pytest.approx(0.3, abs=0.05)


@pytest.mark.asyncio
async def test_animate_skips_when_not_running_under_lock(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("lock")
    show = manager.ensure_room(room)
    show.running = False
    light = LightInfo("light.den", None, "color", False, 0.3)

    calls: list = []

    async def capture_call(self, *args, **kwargs):
        calls.append(args)

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
    ):
        await manager._animate_room(show)

    assert calls == []
