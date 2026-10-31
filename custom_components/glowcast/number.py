"""Speed and brightness number entities."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_BRIGHTNESS, KEY_SPEED
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
    entities: list[NumberEntity] = []
    for room in rooms.values():
        entities.append(GlowCastSpeedNumber(manager, room))
        entities.append(GlowCastBrightnessNumber(manager, room))
    async_add_entities(entities)

    async def _on_rooms(_event):
        rooms_now = build_rooms(hass)
        hass.data[DOMAIN]["rooms"] = rooms_now
        manager.sync_rooms(rooms_now)
        existing = {e.unique_id for e in entities}
        new = []
        for room in rooms_now.values():
            for cls, key in (
                (GlowCastSpeedNumber, KEY_SPEED),
                (GlowCastBrightnessNumber, KEY_BRIGHTNESS),
            ):
                uid = f"{DOMAIN}_{room.room_id}_{key}"
                if uid not in existing:
                    ent = cls(manager, room)
                    new.append(ent)
                    entities.append(ent)
        if new:
            async_add_entities(new)

    entry.async_on_unload(hass.bus.async_listen(f"{DOMAIN}_rooms_updated", _on_rooms))


class GlowCastSpeedNumber(GlowCastRoomEntity, NumberEntity):
    _attr_translation_key = "speed"
    _attr_name = "Speed"
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, manager: ShowManager, room: Room) -> None:
        super().__init__(manager, room, KEY_SPEED)

    @property
    def native_value(self) -> float:
        return float(self.show.speed)

    async def async_set_native_value(self, value: float) -> None:
        self.show.speed = int(value)
        self.async_write_ha_state()
        if self.show.running:
            self._manager.wake_show(self._room.room_id)


class GlowCastBrightnessNumber(GlowCastRoomEntity, NumberEntity):
    _attr_translation_key = "brightness"
    _attr_name = "Brightness"
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, manager: ShowManager, room: Room) -> None:
        super().__init__(manager, room, KEY_BRIGHTNESS)

    @property
    def native_value(self) -> float:
        return float(self.show.brightness)

    async def async_set_native_value(self, value: float) -> None:
        self.show.brightness = int(value)
        self.async_write_ha_state()
        if self.show.running:
            self._manager.wake_show(self._room.room_id)
