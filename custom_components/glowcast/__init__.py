"""GlowCast: themed animated light shows for existing Home Assistant lights."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_ROOM,
    ATTR_THEME,
    DOMAIN,
    KEY_BRIGHTNESS,
    KEY_COLOR,
    KEY_EFFECT,
    KEY_MODE,
    KEY_SHOW,
    KEY_SPEED,
    KEY_THEME,
    PLATFORMS,
    SERVICE_SET_COLOR,
    SERVICE_START_THEME,
    SERVICE_STOP,
)
from .rooms import build_rooms
from .show import ShowManager

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_ENTITY_KEYS = (
    KEY_THEME,
    KEY_MODE,
    KEY_EFFECT,
    KEY_SHOW,
    KEY_SPEED,
    KEY_BRIGHTNESS,
    KEY_COLOR,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up GlowCast (YAML not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up GlowCast from a config entry."""
    manager = ShowManager(hass)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["manager"] = manager
    hass.data[DOMAIN]["entry_id"] = entry.entry_id

    rooms = build_rooms(hass)
    manager.sync_rooms(rooms)
    hass.data[DOMAIN]["rooms"] = rooms

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(p) for p in PLATFORMS]
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Registry listeners: lights moving into areas join automatically
    @callback
    def _registry_updated(event) -> None:
        _refresh_rooms(hass)

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_updated)
    )
    entry.async_on_unload(
        hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, _registry_updated)
    )

    async def _on_started(_event) -> None:
        _refresh_rooms(hass)

    if hass.is_running:
        _refresh_rooms(hass)
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
        )

    _register_services(hass)
    return True


def _refresh_rooms(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN) or {}
    manager: ShowManager | None = data.get("manager")
    if manager is None:
        return
    rooms = build_rooms(hass)
    manager.sync_rooms(rooms)
    data["rooms"] = rooms
    hass.bus.async_fire(f"{DOMAIN}_rooms_updated")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _refresh_rooms(hass)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager: ShowManager | None = hass.data.get(DOMAIN, {}).get("manager")
    if manager:
        await manager.async_unload()
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform(p) for p in PLATFORMS]
    )
    if unload_ok:
        hass.data.pop(DOMAIN, None)
        # Services stay registered for domain lifetime; remove if no entries
        if not hass.config_entries.async_entries(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_START_THEME)
            hass.services.async_remove(DOMAIN, SERVICE_STOP)
            hass.services.async_remove(DOMAIN, SERVICE_SET_COLOR)
    return unload_ok


def _room_id_from_unique_id(unique_id: str) -> str | None:
    """Extract room_id from glowcast_{room_id}_{key} unique_id."""
    prefix = f"{DOMAIN}_"
    if not unique_id.startswith(prefix):
        return None
    rest = unique_id[len(prefix) :]
    for key in _ENTITY_KEYS:
        suffix = f"_{key}"
        if rest.endswith(suffix):
            return rest[: -len(suffix)]
    return None


def resolve_room_from_call(hass: HomeAssistant, call: ServiceCall) -> str | None:
    """Resolve room id from `room` field or a GlowCast entity/device target."""
    room = call.data.get(ATTR_ROOM)
    if room:
        return str(room)

    ent_reg = er.async_get(hass)
    for eid in cv.ensure_list(call.data.get("entity_id") or []):
        entry = ent_reg.async_get(eid)
        if entry is None or entry.platform != DOMAIN:
            continue
        rid = _room_id_from_unique_id(entry.unique_id)
        if rid:
            return rid

    dev_reg = dr.async_get(hass)
    for did in cv.ensure_list(call.data.get("device_id") or []):
        device = dev_reg.async_get(did)
        if device is None:
            continue
        for domain, ident in device.identifiers:
            if domain == DOMAIN:
                return ident
    return None


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_START_THEME):
        return

    async def handle_start(call: ServiceCall) -> None:
        manager: ShowManager = hass.data[DOMAIN]["manager"]
        room = call.data[ATTR_ROOM]
        theme = call.data.get(ATTR_THEME)
        await manager.start(room, theme)

    async def handle_stop(call: ServiceCall) -> None:
        manager: ShowManager = hass.data[DOMAIN]["manager"]
        room = call.data.get(ATTR_ROOM)
        if room:
            await manager.stop(room)
        else:
            await manager.stop_all()

    async def handle_set_color(call: ServiceCall) -> None:
        manager: ShowManager = hass.data[DOMAIN]["manager"]
        room = resolve_room_from_call(hass, call)
        if not room:
            _LOGGER.warning("set_color requires room or a GlowCast entity/device target")
            return
        rgb = call.data[ATTR_RGB_COLOR]
        brightness = call.data.get(ATTR_BRIGHTNESS)
        await manager.set_color(room, tuple(rgb), brightness)

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_THEME,
        handle_start,
        schema=vol.Schema(
            {
                vol.Required(ATTR_ROOM): cv.string,
                vol.Optional(ATTR_THEME): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP,
        handle_stop,
        schema=vol.Schema({vol.Optional(ATTR_ROOM): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_COLOR,
        handle_set_color,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ROOM): cv.string,
                vol.Required(ATTR_RGB_COLOR): vol.All(
                    vol.ExactSequence(
                        (
                            vol.All(vol.Coerce(int), vol.Range(0, 255)),
                            vol.All(vol.Coerce(int), vol.Range(0, 255)),
                            vol.All(vol.Coerce(int), vol.Range(0, 255)),
                        )
                    ),
                ),
                vol.Optional(ATTR_BRIGHTNESS): vol.All(
                    vol.Coerce(int), vol.Range(0, 100)
                ),
                vol.Optional("entity_id"): cv.entity_ids,
                vol.Optional("device_id"): vol.Any(cv.string, [cv.string]),
            }
        ),
    )
