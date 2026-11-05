"""Solid-color set_color service, custom_color theme, and light entity."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.glowcast.const import (
    DEFAULT_WARM_WHITE,
    DOMAIN,
    MODE_AUTO,
    MODE_MANUAL,
    SERVICE_SET_COLOR,
    THEME_CUSTOM_COLOR,
)
from custom_components.glowcast.light import GlowCastColorLight
from custom_components.glowcast.lights import (
    CAP_COLOR,
    CAP_COLOR_TEMP,
    CAP_ONOFF,
    LightInfo,
)
from custom_components.glowcast.rooms import Room
from custom_components.glowcast.show import ShowManager
from custom_components.glowcast.themes import THEMES, all_theme_options

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant, ServiceRegistry
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


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


def test_custom_color_in_theme_options():
    keys = [k for k, _ in all_theme_options()]
    assert THEME_CUSTOM_COLOR in keys
    assert THEME_CUSTOM_COLOR in THEMES
    assert THEMES[THEME_CUSTOM_COLOR].default_effect == "static"


@pytest.mark.asyncio
async def test_set_color_from_idle_snapshots_and_starts(hass: HomeAssistant):
    """From idle: snapshot, start loop, manual + custom_color, apply RGB."""
    manager = ShowManager(hass)
    room = _room()
    show = manager.ensure_room(room)
    show.mode = MODE_AUTO
    show.theme_key = "halloween"
    light = LightInfo("light.den", None, CAP_COLOR, slow=False, min_interval=0.01)
    hass.states.async_set("light.den", "on", {"supported_color_modes": ["rgb"]})

    snap = _saved_off()
    calls: list[dict] = []

    async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        if domain == "light" and service == "turn_on":
            calls.append(dict(service_data or {}))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch(
            "custom_components.glowcast.show.snapshot_states",
            return_value=dict(snap),
        ) as snap_mock,
        patch.object(ServiceRegistry, "async_call", new=capture_call),
    ):
        await manager.set_color(room.room_id, (255, 32, 64), brightness=70)
        # Let the background loop render at least one frame
        for _ in range(20):
            if calls:
                break
            await asyncio.sleep(0.05)

    assert show.running is True
    assert show.mode == MODE_MANUAL
    assert show.theme_key == THEME_CUSTOM_COLOR
    assert show.effect == "static"
    assert show.rgb_color == (255, 32, 64)
    assert show.brightness == 70
    assert show._saved == snap
    snap_mock.assert_called_once()
    assert any(c.get("rgb_color") == [255, 32, 64] for c in calls)

    show.running = False
    show._wake.set()
    if show._task:
        show._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await show._task


@pytest.mark.asyncio
async def test_set_color_during_show_hot_swap_no_restore(hass: HomeAssistant):
    """While running: restart into custom_color/static; no restore/re-snapshot."""
    manager = ShowManager(hass)
    room = _room("hot")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, CAP_COLOR, False, 0.01)
    hass.states.async_set("light.den", "on", {"supported_color_modes": ["rgb"]})

    show._saved = _saved_off()
    show.running = True
    show.mode = MODE_MANUAL
    show.theme_key = "calm"
    show.effect = "breathe"
    show._task = hass.async_create_task(asyncio.sleep(60))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(manager, "_animate_room", new_callable=AsyncMock),
        patch.object(manager, "_restore", new_callable=AsyncMock) as restore_mock,
        patch(
            "custom_components.glowcast.show.snapshot_states",
            return_value={"poison": True},
        ) as snap,
    ):
        saved_before = dict(show._saved)
        await manager.set_color(room.room_id, [10, 20, 30])

        assert show.running is True
        assert show.theme_key == THEME_CUSTOM_COLOR
        assert show.effect == "static"
        assert show.mode == MODE_MANUAL
        assert show.rgb_color == (10, 20, 30)
        assert show._saved == saved_before
        theme = manager.current_theme(show)
        assert theme.palette == ((10, 20, 30),)
        snap.assert_not_called()
        restore_mock.assert_not_awaited()
        assert manager._active_task_count() == 1

        await manager.stop(room.room_id, restore=False)


@pytest.mark.asyncio
async def test_stop_restores_after_set_color(hass: HomeAssistant):
    """Stop after set_color restores the original snapshot."""
    manager = ShowManager(hass)
    room = _room("end")
    show = manager.ensure_room(room)
    light = LightInfo("light.den", None, CAP_COLOR, slow=True, min_interval=1.0)
    import time
    manager.rate.last_sent["light.den"] = time.monotonic()
    hass.states.async_set("light.den", "on")

    show.running = True
    show._saved = _saved_off()
    show.theme_key = THEME_CUSTOM_COLOR
    show.rgb_color = (1, 2, 3)
    show.mode = MODE_MANUAL

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

    calls: list[tuple] = []

    async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        calls.append((domain, service, dict(service_data or {}), blocking))

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
        patch("custom_components.glowcast.show.asyncio.sleep", new_callable=AsyncMock),
    ):
        await manager.stop(room.room_id)

    assert cancelled.is_set()
    assert show.running is False
    assert show._task is None
    assert len(calls) == 1
    assert calls[0][1] == "turn_off"
    assert calls[0][3] is True
    assert show._saved == {}


@pytest.mark.asyncio
async def test_set_color_white_only_and_onoff(hass: HomeAssistant):
    """Color-temp lights get Kelvin; on/off lights just turn on."""
    manager = ShowManager(hass)
    room = _room("caps")
    show = manager.ensure_room(room)
    show.mode = MODE_MANUAL
    show.theme_key = THEME_CUSTOM_COLOR
    show.rgb_color = (255, 100, 20)
    show.effect = "static"
    show.brightness = 80
    show.running = True

    ct = LightInfo("light.ct", None, CAP_COLOR_TEMP, False, 0.0)
    onoff = LightInfo("light.basic", None, CAP_ONOFF, False, 0.0)
    rgb = LightInfo("light.rgb", None, CAP_COLOR, False, 0.0)
    hass.states.async_set("light.ct", "on", {"supported_color_modes": ["color_temp"]})
    hass.states.async_set("light.basic", "on")
    hass.states.async_set("light.rgb", "on", {"supported_color_modes": ["rgb"]})

    calls: list[dict] = []

    async def capture_call(self, domain, service, service_data=None, blocking=False, **kwargs):
        calls.append(dict(service_data or {}))

    with (
        patch.object(room, "resolve_lights", return_value=[rgb, ct, onoff]),
        patch.object(ServiceRegistry, "async_call", new=capture_call),
    ):
        await manager._animate_room(show)

    by_id = {c["entity_id"]: c for c in calls}
    assert by_id["light.rgb"]["rgb_color"] == [255, 100, 20]
    assert "color_temp_kelvin" in by_id["light.ct"]
    assert by_id["light.ct"]["color_temp_kelvin"] < 4250  # warm-ish
    assert by_id["light.basic"] == {"entity_id": "light.basic"}


@pytest.mark.asyncio
async def test_set_color_forces_manual_mode(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("mode")
    show = manager.ensure_room(room)
    show.mode = MODE_AUTO
    light = LightInfo("light.den", None, CAP_COLOR, False, 0.01)

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch(
            "custom_components.glowcast.show.snapshot_states",
            return_value=_saved_off(),
        ),
        patch.object(ServiceRegistry, "async_call", new=AsyncMock()),
    ):
        await manager.set_color(room.room_id, (0, 255, 0))

    assert show.mode == MODE_MANUAL
    assert show.theme_key == THEME_CUSTOM_COLOR

    show.running = False
    show._wake.set()
    if show._task:
        show._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await show._task


@pytest.mark.asyncio
async def test_set_color_defaults_brightness_to_room(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = _room("bri")
    show = manager.ensure_room(room)
    show.brightness = 55
    light = LightInfo("light.den", None, CAP_COLOR, False, 0.01)

    with (
        patch.object(room, "resolve_lights", return_value=[light]),
        patch(
            "custom_components.glowcast.show.snapshot_states",
            return_value=_saved_off(),
        ),
        patch.object(ServiceRegistry, "async_call", new=AsyncMock()),
    ):
        await manager.set_color(room.room_id, (1, 2, 3))  # no brightness arg

    assert show.brightness == 55

    show.running = False
    show._wake.set()
    if show._task:
        show._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await show._task


@pytest.mark.asyncio
async def test_theme_and_switch_expose_rgb_color_attr(hass: HomeAssistant):
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    living = area_reg.async_create("Color Room")
    light = ent_reg.async_get_or_create(
        "light", "test", "cr1", suggested_object_id="color_room_lamp"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=living.id)
    hass.states.async_set(light.entity_id, "off", {"supported_color_modes": ["rgb"]})

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager: ShowManager = hass.data[DOMAIN]["manager"]
    show = manager.shows[living.id]
    show.rgb_color = (9, 8, 7)

    # Force state write
    hass.bus.async_fire(f"{DOMAIN}_update")
    await hass.async_block_till_done()

    theme_state = hass.states.get("select.glowcast_color_room_theme")
    switch_state = hass.states.get("switch.glowcast_color_room_show")
    assert theme_state is not None
    assert switch_state is not None
    assert theme_state.attributes.get("rgb_color") == [9, 8, 7]
    # Show is off: switch omits rgb_color (theme select still exposes it)
    assert switch_state.attributes.get("rgb_color") is None

    show.running = True
    hass.bus.async_fire(f"{DOMAIN}_update")
    await hass.async_block_till_done()
    switch_state = hass.states.get("switch.glowcast_color_room_show")
    assert switch_state.attributes.get("rgb_color") == [9, 8, 7]
    show.running = False

    opts = theme_state.attributes.get("options") or []
    assert THEME_CUSTOM_COLOR in opts


@pytest.mark.asyncio
async def test_service_set_color_registered(hass: HomeAssistant):
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    area = area_reg.async_create("Svc Room")
    light = ent_reg.async_get_or_create(
        "light", "test", "svc1", suggested_object_id="svc_lamp"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=area.id)
    hass.states.async_set(light.entity_id, "off", {"supported_color_modes": ["rgb"]})

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, SERVICE_SET_COLOR)

    manager: ShowManager = hass.data[DOMAIN]["manager"]
    with patch.object(manager, "set_color", new_callable=AsyncMock) as mock_set:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_COLOR,
            {"room": area.id, "rgb_color": [1, 2, 3], "brightness": 40},
            blocking=True,
        )
    mock_set.assert_awaited_once_with(area.id, (1, 2, 3), 40)


@pytest.mark.asyncio
async def test_light_entity_turn_on_off(hass: HomeAssistant):
    """light.turn_on calls set_color; turn_off stops/restores; is_on tracks running."""
    manager = ShowManager(hass)
    room = _room("lamp")
    show = manager.ensure_room(room)
    show.rgb_color = None
    entity = GlowCastColorLight(manager, room)
    entity.hass = hass
    entity.entity_id = "light.glowcast_lamp_color"

    with patch.object(manager, "set_color", new_callable=AsyncMock) as mock_set:
        await entity.async_turn_on(rgb_color=(100, 50, 25), brightness=128)
    mock_set.assert_awaited_once()
    args = mock_set.await_args.args
    assert args[0] == "lamp"
    assert args[1] == (100, 50, 25)
    assert args[2] == 50  # 128/255 ≈ 50%

    # No color → last / default warm white
    show.rgb_color = None
    with patch.object(manager, "set_color", new_callable=AsyncMock) as mock_set:
        await entity.async_turn_on()
    assert mock_set.await_args.args[1] == DEFAULT_WARM_WHITE

    show.rgb_color = (11, 22, 33)
    with patch.object(manager, "set_color", new_callable=AsyncMock) as mock_set:
        await entity.async_turn_on()
    assert mock_set.await_args.args[1] == (11, 22, 33)

    # hs_color path
    with patch.object(manager, "set_color", new_callable=AsyncMock) as mock_set:
        await entity.async_turn_on(hs_color=(0, 100))
    rgb = mock_set.await_args.args[1]
    assert rgb[0] == 255 and rgb[1] == 0 and rgb[2] == 0

    show.running = True
    assert entity.is_on is True
    with patch.object(manager, "stop", new_callable=AsyncMock) as mock_stop:
        await entity.async_turn_off()
    mock_stop.assert_awaited_once_with("lamp")


@pytest.mark.asyncio
async def test_light_platform_created_on_setup(hass: HomeAssistant):
    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    area = area_reg.async_create("Lite Room")
    light = ent_reg.async_get_or_create(
        "light", "test", "lite1", suggested_object_id="lite_lamp"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=area.id)
    hass.states.async_set(light.entity_id, "off", {"supported_color_modes": ["rgb"]})

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("light.glowcast_lite_room_color")
    assert state is not None
    assert state.state == "off"

    # unique_id
    gc = ent_reg.async_get("light.glowcast_lite_room_color")
    assert gc is not None
    assert gc.unique_id == f"{DOMAIN}_{area.id}_color"
