"""Show on/off switch per room."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_SHOW
from .entity import GlowCastRoomEntity
from .rooms import Room, build_rooms
from .show import ShowManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: ShowManager = hass.data[DOMAIN]["manager"]
    rooms = hass.data[DOMAIN].get("rooms") or build_rooms(hass)
    entities: list[GlowCastShowSwitch] = [
        GlowCastShowSwitch(manager, room) for room in rooms.values()
    ]
    async_add_entities(entities)

    async def _on_rooms(_event):
        rooms_now = build_rooms(hass)
        hass.data[DOMAIN]["rooms"] = rooms_now
        manager.sync_rooms(rooms_now)
        existing = {e.unique_id for e in entities}
        new = []
        for room in rooms_now.values():
            uid = f"{DOMAIN}_{room.room_id}_{KEY_SHOW}"
            if uid not in existing:
                ent = GlowCastShowSwitch(manager, room)
                new.append(ent)
                entities.append(ent)
        if new:
            async_add_entities(new)

    entry.async_on_unload(hass.bus.async_listen(f"{DOMAIN}_rooms_updated", _on_rooms))


class GlowCastShowSwitch(GlowCastRoomEntity, SwitchEntity):
    _attr_translation_key = "show"
    _attr_name = "Show"

    def __init__(self, manager: ShowManager, room: Room) -> None:
        super().__init__(manager, room, KEY_SHOW)

    @property
    def is_on(self) -> bool:
        return self.show.running

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose rgb_color only while a show / solid color is active."""
        show = self.show
        if not show.running or not show.rgb_color:
            return {}
        return {"rgb_color": list(show.rgb_color)}

    async def async_turn_on(self, **kwargs) -> None:
        await self._manager.start(self._room.room_id)

    async def async_turn_off(self, **kwargs) -> None:
        await self._manager.stop(self._room.room_id)
