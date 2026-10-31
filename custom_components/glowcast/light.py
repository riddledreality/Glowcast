"""Per-room solid-color light entity for GlowCast."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import color_hs_to_RGB

from .const import DEFAULT_WARM_WHITE, DOMAIN, KEY_COLOR
from .entity import GlowCastRoomEntity
from .rooms import Room, build_rooms
from .show import ShowManager
from .themes import brightness_pct_to_ha


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: ShowManager = hass.data[DOMAIN]["manager"]
    rooms = hass.data[DOMAIN].get("rooms") or build_rooms(hass)
    entities: list[GlowCastColorLight] = [
        GlowCastColorLight(manager, room) for room in rooms.values()
    ]
    async_add_entities(entities)

    async def _on_rooms(_event):
        rooms_now = build_rooms(hass)
        hass.data[DOMAIN]["rooms"] = rooms_now
        manager.sync_rooms(rooms_now)
        existing = {e.unique_id for e in entities}
        new = []
        for room in rooms_now.values():
            uid = f"{DOMAIN}_{room.room_id}_{KEY_COLOR}"
            if uid not in existing:
                ent = GlowCastColorLight(manager, room)
                new.append(ent)
                entities.append(ent)
        if new:
            async_add_entities(new)

    entry.async_on_unload(hass.bus.async_listen(f"{DOMAIN}_rooms_updated", _on_rooms))


class GlowCastColorLight(GlowCastRoomEntity, LightEntity):
    """Room light that drives GlowCast solid-color / show state."""

    _attr_translation_key = "color"
    _attr_name = "Color"
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, manager: ShowManager, room: Room) -> None:
        super().__init__(manager, room, KEY_COLOR)

    @property
    def is_on(self) -> bool:
        return self.show.running

    @property
    def brightness(self) -> int | None:
        return brightness_pct_to_ha(self.show.brightness)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self.show.rgb_color or DEFAULT_WARM_WHITE

    async def async_turn_on(self, **kwargs: Any) -> None:
        rgb = kwargs.get(ATTR_RGB_COLOR)
        if rgb is None and ATTR_HS_COLOR in kwargs:
            hs = kwargs[ATTR_HS_COLOR]
            rgb = color_hs_to_RGB(float(hs[0]), float(hs[1]))
        if rgb is None:
            rgb = self.show.rgb_color or DEFAULT_WARM_WHITE

        brightness_pct: int | None = None
        if ATTR_BRIGHTNESS in kwargs:
            bri = int(kwargs[ATTR_BRIGHTNESS])
            brightness_pct = max(0, min(100, round(bri * 100 / 255)))

        await self._manager.set_color(
            self._room.room_id, tuple(int(c) for c in rgb), brightness_pct
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._manager.stop(self._room.room_id)
