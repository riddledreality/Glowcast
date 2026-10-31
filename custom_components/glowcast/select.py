"""Select entities: theme, mode, effect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CUSTOM_THEMES,
    DOMAIN,
    EFFECTS,
    KEY_EFFECT,
    KEY_MODE,
    KEY_THEME,
    MODE_AUTO,
    MODE_MANUAL,
)
from .entity import GlowCastRoomEntity
from .rooms import Room, build_rooms
from .show import ShowManager
from .themes import THEMES, all_theme_options


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: ShowManager = hass.data[DOMAIN]["manager"]
    rooms = hass.data[DOMAIN].get("rooms") or build_rooms(hass)
    entities: list[SelectEntity] = []
    for room in rooms.values():
        entities.extend(
            [
                GlowCastThemeSelect(manager, room, entry),
                GlowCastModeSelect(manager, room),
                GlowCastEffectSelect(manager, room),
            ]
        )
    async_add_entities(entities)

    async def _on_rooms(_event):
        current = {e.unique_id for e in entities}
        rooms_now = build_rooms(hass)
        hass.data[DOMAIN]["rooms"] = rooms_now
        manager.sync_rooms(rooms_now)
        fresh: list[SelectEntity] = []
        for room in rooms_now.values():
            candidates = [
                GlowCastThemeSelect(manager, room, entry),
                GlowCastModeSelect(manager, room),
                GlowCastEffectSelect(manager, room),
            ]
            for ent in candidates:
                if ent.unique_id not in current:
                    fresh.append(ent)
                    entities.append(ent)
        if fresh:
            async_add_entities(fresh)

    entry.async_on_unload(hass.bus.async_listen(f"{DOMAIN}_rooms_updated", _on_rooms))


class GlowCastThemeSelect(GlowCastRoomEntity, SelectEntity):
    _attr_translation_key = "theme"
    _attr_name = "Theme"

    def __init__(self, manager: ShowManager, room: Room, entry: ConfigEntry) -> None:
        super().__init__(manager, room, KEY_THEME)
        self._entry = entry

    @property
    def options(self) -> list[str]:
        custom = self._entry.options.get(CONF_CUSTOM_THEMES) or []
        return [k for k, _ in all_theme_options(custom)]

    @property
    def current_option(self) -> str | None:
        return self.show.theme_key

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rgb = self.show.rgb_color
        return {"rgb_color": list(rgb) if rgb else None}

    async def async_select_option(self, option: str) -> None:
        self.show.theme_key = option
        self.show.mode = MODE_MANUAL
        if option in THEMES:
            self.show.effect = THEMES[option].default_effect
        # Publish HA state before any awaits so the UI feels instant.
        self.async_write_ha_state()
        self._manager._notify()
        if self.show.running:
            # Hot-swap into the live loop — never stop/restore/restart.
            self._manager.wake_show(self._room.room_id)


class GlowCastModeSelect(GlowCastRoomEntity, SelectEntity):
    _attr_translation_key = "mode"
    _attr_name = "Mode"
    _attr_options = [MODE_AUTO, MODE_MANUAL]

    def __init__(self, manager: ShowManager, room: Room) -> None:
        super().__init__(manager, room, KEY_MODE)

    @property
    def current_option(self) -> str | None:
        return self.show.mode

    async def async_select_option(self, option: str) -> None:
        self.show.mode = option
        if option == MODE_AUTO:
            # Immediately pick today's holiday/theme (do not wait for midnight)
            self._manager.apply_auto_theme(self.show)
        self.async_write_ha_state()
        self._manager._notify()
        if self.show.running:
            self._manager.wake_show(self._room.room_id)


class GlowCastEffectSelect(GlowCastRoomEntity, SelectEntity):
    _attr_translation_key = "effect"
    _attr_name = "Effect"
    _attr_options = list(EFFECTS)

    def __init__(self, manager: ShowManager, room: Room) -> None:
        super().__init__(manager, room, KEY_EFFECT)

    @property
    def current_option(self) -> str | None:
        return self.show.effect

    async def async_select_option(self, option: str) -> None:
        self.show.effect = option
        self.show.mode = MODE_MANUAL
        self.async_write_ha_state()
        self._manager._notify()
        if self.show.running:
            self._manager.wake_show(self._room.room_id)
