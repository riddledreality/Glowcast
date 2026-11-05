"""Rate limiter must persist last-sent times across frames (no resolve_lights mock)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from custom_components.glowcast.const import DEFAULT_FAST_MIN_INTERVAL, DOMAIN
from custom_components.glowcast.rooms import Room
from custom_components.glowcast.show import ShowManager

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant, ServiceRegistry
from homeassistant.helpers import entity_registry as er


@pytest.mark.asyncio
async def test_rate_cap_persists_across_frames_without_mocking_resolve(hass: HomeAssistant):
    """collect_lights rebuilds LightInfo every frame — last-sent must still stick.

    This test intentionally does NOT mock Room.resolve_lights / collect_lights.
    """
    assert DEFAULT_FAST_MIN_INTERVAL >= 0.5

    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "light", "test", "persist1", suggested_object_id="persist_lamp"
    )
    hass.states.async_set(
        entry.entity_id, "on", {"supported_color_modes": ["rgb"], "brightness": 100}
    )

    manager = ShowManager(hass)
    room = Room(
        room_id="custom_persist",
        name="Persist",
        kind="custom",
        entity_ids=[entry.entity_id],
    )
    show = manager.ensure_room(room)
    show.running = True
    show.mode = "manual"
    show.theme_key = "calm"
    show.effect = "static"

    calls: list[str] = []

    async def capture(self, domain, service, service_data=None, blocking=False, **kwargs):
        calls.append((service_data or {}).get("entity_id"))
        return None

    with patch.object(ServiceRegistry, "async_call", new=capture):
        await manager._animate_room(show)
        assert calls == [entry.entity_id]
        # Immediate second frame: persisted last_sent must suppress the call
        await manager._animate_room(show)
        assert calls == [entry.entity_id]
        # After interval elapses, allow another
        manager.rate.last_sent[entry.entity_id] = 0.0
        manager.rate.in_flight[entry.entity_id] = False
        await manager._animate_room(show)
        assert calls == [entry.entity_id, entry.entity_id]


@pytest.mark.asyncio
async def test_settle_uses_persisted_last_sent(hass: HomeAssistant):
    manager = ShowManager(hass)
    room = Room(room_id="s", name="S", kind="custom", entity_ids=["light.s"])
    show = manager.ensure_room(room)
    hass.states.async_set("light.s", "on", {"supported_color_modes": ["rgb"]})
    # Seed rate book as if we just sent
    import time
    manager.rate.last_sent["light.s"] = time.monotonic()

    slept: list[float] = []

    async def fake_sleep(delay):
        slept.append(delay)

    with patch("custom_components.glowcast.show.asyncio.sleep", side_effect=fake_sleep):
        # resolve_lights will rebuild LightInfo — settle must still see last_sent
        await manager._settle_before_restore(show)

    assert slept, "expected settle sleep from persisted last_sent"
    assert slept[0] == pytest.approx(0.5, abs=0.15) or slept[0] > 0.3
