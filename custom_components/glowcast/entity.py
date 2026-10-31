"""Shared entity base for GlowCast room controls."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .rooms import Room
from .show import RoomShow, ShowManager


class GlowCastRoomEntity(Entity):
    """Base entity tied to a GlowCast room."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: ShowManager, room: Room, key: str) -> None:
        self._manager = manager
        self._room = room
        self._key = key
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.room_id)},
            name=f"GlowCast {room.name}",
            manufacturer="GlowCast",
            model="Light show room",
        )

    @property
    def show(self) -> RoomShow:
        return self._manager.ensure_room(self._room)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_update", self._handle_update)
        )
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_rooms_updated", self._handle_update)
        )

    async def _handle_update(self, _event) -> None:
        self.async_write_ha_state()
