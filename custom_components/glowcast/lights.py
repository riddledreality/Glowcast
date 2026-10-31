"""Light discovery, capabilities, and rate limiting for GlowCast."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_EXCLUDED_LIGHTS,
    CONF_FAST_MIN_INTERVAL,
    CONF_SLOW_LIGHTS,
    CONF_SLOW_MIN_INTERVAL,
    DEFAULT_FAST_MIN_INTERVAL,
    DEFAULT_SLOW_MIN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Prefer modern color modes; fall back to supported_features bits when needed.
SUPPORT_BRIGHTNESS = 1
SUPPORT_COLOR_TEMP = 2
SUPPORT_COLOR = 16

CAP_ONOFF = "onoff"
CAP_BRIGHTNESS = "brightness"
CAP_COLOR_TEMP = "color_temp"
CAP_COLOR = "color"


@dataclass
class LightInfo:
    """Resolved light entity with capability + rate-limit metadata."""

    entity_id: str
    area_id: str | None
    capability: str  # color | color_temp | brightness | onoff
    slow: bool
    min_interval: float
    _last_update: float = field(default=0.0, repr=False)

    def can_update(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return (now - self._last_update) >= self.min_interval

    def mark_updated(self, now: float | None = None) -> None:
        self._last_update = time.monotonic() if now is None else now


def detect_capability(state: State | None) -> str:
    """Classify a light entity from its state attributes."""
    if state is None:
        return CAP_ONOFF
    attrs = state.attributes
    color_modes = attrs.get("supported_color_modes") or attrs.get("supported_color_mode")
    if isinstance(color_modes, str):
        color_modes = {color_modes}
    if color_modes:
        modes = set(color_modes)
        if modes & {"rgb", "rgbw", "rgbww", "hs", "xy"}:
            return CAP_COLOR
        if modes & {"color_temp"}:
            return CAP_COLOR_TEMP
        if modes & {"brightness", "white"}:
            return CAP_BRIGHTNESS
        return CAP_ONOFF

    features = attrs.get(ATTR_SUPPORTED_FEATURES, 0) or 0
    try:
        features = int(features)
    except (TypeError, ValueError):
        features = 0
    if features & SUPPORT_COLOR:
        return CAP_COLOR
    if features & SUPPORT_COLOR_TEMP:
        return CAP_COLOR_TEMP
    if features & SUPPORT_BRIGHTNESS:
        return CAP_BRIGHTNESS
    return CAP_ONOFF


def _options(hass: HomeAssistant) -> dict[str, Any]:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    return dict(entries[0].options or {})


def is_slow_light(entity_id: str, options: dict[str, Any], state: State | None) -> bool:
    """Heuristic + user list for Bluetooth / Govee-style slow lights."""
    slow_list = set(options.get(CONF_SLOW_LIGHTS) or [])
    if entity_id in slow_list:
        return True
    # Heuristics: BLE / Govee platforms commonly need throttling
    if state is not None:
        platform = (state.attributes.get("platform") or "").lower()
        if any(x in platform for x in ("govee", "ble", "bluetooth")):
            return True
    eid = entity_id.lower()
    if any(x in eid for x in ("govee", "_ble", "bluetooth")):
        return True
    return False


def collect_lights(hass: HomeAssistant) -> list[LightInfo]:
    """Return non-excluded light entities with capability metadata."""
    options = _options(hass)
    excluded = set(options.get(CONF_EXCLUDED_LIGHTS) or [])
    slow_min = float(options.get(CONF_SLOW_MIN_INTERVAL, DEFAULT_SLOW_MIN_INTERVAL))
    fast_min = float(options.get(CONF_FAST_MIN_INTERVAL, DEFAULT_FAST_MIN_INTERVAL))

    ent_reg = er.async_get(hass)
    lights: list[LightInfo] = []
    for state in hass.states.async_all("light"):
        entity_id = state.entity_id
        if entity_id in excluded:
            continue
        entry = ent_reg.async_get(entity_id)
        # Skip GlowCast's own entities if any ever appear under light domain
        if entry and entry.platform == DOMAIN:
            continue
        capability = detect_capability(state)
        slow = is_slow_light(entity_id, options, state)
        lights.append(
            LightInfo(
                entity_id=entity_id,
                area_id=entry.area_id if entry else None,
                capability=capability,
                slow=slow,
                min_interval=slow_min if slow else fast_min,
            )
        )
    return lights


def lights_for_area(hass: HomeAssistant, area_id: str) -> list[LightInfo]:
    return [li for li in collect_lights(hass) if li.area_id == area_id]


def lights_by_ids(hass: HomeAssistant, entity_ids: list[str]) -> list[LightInfo]:
    wanted = set(entity_ids)
    return [li for li in collect_lights(hass) if li.entity_id in wanted]


def areas_with_lights(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Return (area_id, area_name) for areas that currently contain lights."""
    area_reg = ar.async_get(hass)
    found: dict[str, str] = {}
    for li in collect_lights(hass):
        if not li.area_id:
            continue
        area = area_reg.async_get_area(li.area_id)
        if area:
            found[area.id] = area.name
    return sorted(found.items(), key=lambda x: x[1].lower())


def snapshot_states(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Capture state + relevant attributes for later restore."""
    snaps: dict[str, dict[str, Any]] = {}
    for eid in entity_ids:
        state = hass.states.get(eid)
        if state is None:
            continue
        attrs = state.attributes
        snaps[eid] = {
            "state": state.state,
            "brightness": attrs.get("brightness"),
            "hs_color": attrs.get("hs_color"),
            "rgb_color": attrs.get("rgb_color"),
            "color_temp_kelvin": attrs.get("color_temp_kelvin") or attrs.get("color_temp"),
            "color_mode": attrs.get("color_mode"),
        }
    return snaps
