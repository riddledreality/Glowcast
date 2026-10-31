"""Light discovery, capabilities, and persistent rate limiting for GlowCast."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
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

SUPPORT_BRIGHTNESS = 1
SUPPORT_COLOR_TEMP = 2
SUPPORT_COLOR = 16

CAP_ONOFF = "onoff"
CAP_BRIGHTNESS = "brightness"
CAP_COLOR_TEMP = "color_temp"
CAP_COLOR = "color"

_MAX_BACKOFF = 8.0


@dataclass
class LightInfo:
    """Resolved light entity with capability + rate-limit metadata.

    NOTE: collect_lights() rebuilds these every call. Do NOT store mutable
    pacing state on LightInfo — use LightRateBook on ShowManager instead.
    """

    entity_id: str
    area_id: str | None
    capability: str  # color | color_temp | brightness | onoff
    slow: bool
    min_interval: float


@dataclass
class LightRateBook:
    """Persists last-sent / in-flight / backoff across frames.

    collect_lights() rebuilds LightInfo every frame (fresh _last_update=0),
    which previously defeated the rate limiter (~5 cmds/s per light). This
    book is the single source of truth for pacing and settle-before-restore.
    """

    last_sent: dict[str, float] = field(default_factory=dict)
    in_flight: dict[str, bool] = field(default_factory=dict)
    backoff_until: dict[str, float] = field(default_factory=dict)

    def can_send(self, entity_id: str, min_interval: float, now: float | None = None) -> bool:
        if self.in_flight.get(entity_id):
            return False
        now = time.monotonic() if now is None else now
        if now < self.backoff_until.get(entity_id, 0.0):
            return False
        last = self.last_sent.get(entity_id, 0.0)
        return (now - last) >= min_interval

    def begin(self, entity_id: str) -> None:
        self.in_flight[entity_id] = True

    def end(
        self,
        entity_id: str,
        *,
        success: bool,
        min_interval: float,
        now: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        self.in_flight[entity_id] = False
        self.last_sent[entity_id] = now
        if success:
            # Decay any active backoff.
            if entity_id in self.backoff_until and self.backoff_until[entity_id] <= now:
                self.backoff_until.pop(entity_id, None)
            return
        # Back off on error / timeout / unavailable.
        gap = max(min_interval, 0.5) * 2
        prev = max(0.0, self.backoff_until.get(entity_id, 0.0) - now)
        self.backoff_until[entity_id] = now + min(_MAX_BACKOFF, max(gap, prev * 1.5))

    def settle_delay(self, entity_ids: list[str], min_intervals: dict[str, float], now: float | None = None) -> float:
        """Seconds to wait so every light's min interval since last_sent has elapsed."""
        now = time.monotonic() if now is None else now
        delay = 0.0
        for eid in entity_ids:
            last = self.last_sent.get(eid)
            if last is None:
                continue
            remaining = min_intervals.get(eid, DEFAULT_FAST_MIN_INTERVAL) - (now - last)
            if remaining > delay:
                delay = remaining
        return max(0.0, delay)

    def clear(self, entity_ids: list[str] | None = None) -> None:
        if entity_ids is None:
            self.last_sent.clear()
            self.in_flight.clear()
            self.backoff_until.clear()
            return
        for eid in entity_ids:
            self.last_sent.pop(eid, None)
            self.in_flight.pop(eid, None)
            self.backoff_until.pop(eid, None)


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
    if state is not None:
        platform = (state.attributes.get("platform") or "").lower()
        if any(x in platform for x in ("govee", "ble", "bluetooth")):
            return True
    eid = entity_id.lower()
    if any(x in eid for x in ("govee", "_ble", "bluetooth")):
        return True
    return False


def _entity_area_id(
    hass: HomeAssistant,
    entry: er.RegistryEntry | None,
) -> str | None:
    """Prefer entity area; fall back to the parent device's area."""
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if not entry.device_id:
        return None
    device = dr.async_get(hass).async_get(entry.device_id)
    if device and device.area_id:
        return device.area_id
    return None


def collect_lights(hass: HomeAssistant) -> list[LightInfo]:
    """Return non-excluded light entities with capability metadata.

    Fresh LightInfo instances every call — pacing lives in LightRateBook.
    """
    options = _options(hass)
    excluded = set(options.get(CONF_EXCLUDED_LIGHTS) or [])
    slow_min = float(options.get(CONF_SLOW_MIN_INTERVAL, DEFAULT_SLOW_MIN_INTERVAL))
    fast_min = float(options.get(CONF_FAST_MIN_INTERVAL, DEFAULT_FAST_MIN_INTERVAL))
    fast_min = max(0.5, fast_min)
    slow_min = max(1.0, max(fast_min, slow_min))

    ent_reg = er.async_get(hass)
    lights: list[LightInfo] = []
    for state in hass.states.async_all("light"):
        entity_id = state.entity_id
        if entity_id in excluded:
            continue
        entry = ent_reg.async_get(entity_id)
        if entry and entry.platform == DOMAIN:
            continue
        capability = detect_capability(state)
        slow = is_slow_light(entity_id, options, state)
        lights.append(
            LightInfo(
                entity_id=entity_id,
                area_id=_entity_area_id(hass, entry),
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
