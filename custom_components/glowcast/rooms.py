"""Room / group model for GlowCast."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_CUSTOM_GROUPS, DOMAIN, ROOM_ALL, ROOM_ALL_NAME
from .lights import LightInfo, areas_with_lights, collect_lights, lights_by_ids, lights_for_area


@dataclass
class Room:
    """A logical group of lights (area-backed, all-lights, or custom)."""

    room_id: str
    name: str
    kind: str  # area | all | custom
    area_id: str | None = None
    entity_ids: list[str] = field(default_factory=list)  # for custom only

    def resolve_lights(self, hass: HomeAssistant) -> list[LightInfo]:
        if self.kind == "all":
            return collect_lights(hass)
        if self.kind == "area" and self.area_id:
            return lights_for_area(hass, self.area_id)
        if self.kind == "custom":
            return lights_by_ids(hass, self.entity_ids)
        return []


def _options(hass: HomeAssistant) -> dict[str, Any]:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    return dict(entries[0].options or {})


def build_rooms(hass: HomeAssistant) -> dict[str, Room]:
    """Build room map: All lights + one per area with lights + custom groups."""
    rooms: dict[str, Room] = {
        ROOM_ALL: Room(room_id=ROOM_ALL, name=ROOM_ALL_NAME, kind="all"),
    }
    for area_id, area_name in areas_with_lights(hass):
        rooms[area_id] = Room(
            room_id=area_id,
            name=area_name,
            kind="area",
            area_id=area_id,
        )
    for group in _options(hass).get(CONF_CUSTOM_GROUPS) or []:
        gid = str(group.get("id") or group.get("name", "group").lower().replace(" ", "_"))
        rooms[gid] = Room(
            room_id=gid,
            name=str(group.get("name") or gid),
            kind="custom",
            entity_ids=list(group.get("lights") or group.get("entity_ids") or []),
        )
    return rooms
