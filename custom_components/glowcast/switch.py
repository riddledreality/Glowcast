"""Show on/off switch per room + master GlowCast enabled kill switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, KEY_ENABLED, KEY_SHOW
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
    entities: list[SwitchEntity] = [
        GlowCastShowSwitch(manager, room) for room in rooms.values()
    ]
    entities.append(GlowCastEnabledSwitch(manager, entry))
    async_add_entities(entities)

    async def _on_rooms(_event):
        rooms_now = build_rooms(hass)
        hass.data[DOMAIN]["rooms"] = rooms_now
        manager.sync_rooms(rooms_now)
        existing = {e.unique_id for e in entities if getattr(e, "unique_id", None)}
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


class GlowCastEnabledSwitch(SwitchEntity, RestoreEntity):
    """Master kill switch: off stops all shows and refuses new ones.

    State is restored across restarts (default on). Room shows themselves
    always start idle after HA restart — they are not restored as running.
    """

    _attr_has_entity_name = True
    _attr_name = "Enabled"
    _attr_translation_key = "enabled"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, manager: ShowManager, entry: ConfigEntry) -> None:
        self._manager = manager
        self._attr_unique_id = f"{DOMAIN}_{KEY_ENABLED}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="GlowCast",
            manufacturer="GlowCast",
            model="Controller",
        )
        self._is_on = True

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == STATE_ON
        else:
            self._is_on = True
        self._manager.enabled = self._is_on
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_update", self._handle_update)
        )

    async def _handle_update(self, _event) -> None:
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self._manager.enabled = True
        self.async_write_ha_state()
        self._manager._notify()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self._manager.enabled = False
        await self._manager.stop_all()
        self.async_write_ha_state()
        self._manager._notify()
