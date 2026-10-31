"""Animation / show engine for GlowCast."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
import time
from typing import Any

from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import (
    CONF_CUSTOM_THEMES,
    CONF_TEAM_COLORS,
    DEFAULT_WARM_WHITE,
    DOMAIN,
    EFFECT_BREATHE,
    EFFECT_FLICKER,
    EFFECT_ROTATE,
    EFFECT_STATIC,
    EFFECT_TWINKLE,
    MODE_AUTO,
    MODE_MANUAL,
    THEME_CUSTOM_COLOR,
)
from .lights import LightInfo, CAP_BRIGHTNESS, CAP_COLOR, CAP_COLOR_TEMP, CAP_ONOFF, snapshot_states
from .rooms import Room
from .themes import (
    Theme,
    brightness_pct_to_ha,
    resolve_theme,
    rgb_to_color_temp_kelvin,
    select_auto_theme,
)

_LOGGER = logging.getLogger(__name__)

# Frame cadence (matches prior 200ms interval). Hot-swap wakes early via Event.
_FRAME_SECONDS = 0.2


class RoomShow:
    """Runtime state for one room's show."""

    def __init__(self, room: Room) -> None:
        self.room = room
        self.running = False
        self.mode = MODE_AUTO
        self.theme_key = "calm"
        self.effect = EFFECT_STATIC
        self.speed = 5  # 1-10
        self.brightness = 80  # 1-100
        self.rgb_color: tuple[int, int, int] | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._saved: dict[str, dict[str, Any]] = {}
        self._tick = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "mode": self.mode,
            "theme_key": self.theme_key,
            "effect": self.effect,
            "speed": self.speed,
            "brightness": self.brightness,
            "rgb_color": list(self.rgb_color) if self.rgb_color else None,
        }


class ShowManager:
    """Owns per-room shows and the animation loop."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.shows: dict[str, RoomShow] = {}
        self._midnight_unsub = async_track_time_change(
            hass, self._on_midnight, hour=0, minute=0, second=0
        )

    def ensure_room(self, room: Room) -> RoomShow:
        if room.room_id not in self.shows:
            show = RoomShow(room)
            self.shows[room.room_id] = show
            # mode defaults to auto — pick today's holiday/theme immediately
            self.apply_auto_theme(show)
        else:
            self.shows[room.room_id].room = room
        return self.shows[room.room_id]

    def sync_rooms(self, rooms: dict[str, Room]) -> None:
        for room in rooms.values():
            self.ensure_room(room)
        # Drop shows for removed rooms (stop first)
        for rid in list(self.shows):
            if rid not in rooms:
                show = self.shows.pop(rid)
                if show.running:
                    self.hass.async_create_task(self.stop(rid))

    def apply_auto_theme(self, show: RoomShow) -> None:
        """Set theme_key/effect from the current auto calendar window."""
        if show.mode != MODE_AUTO:
            return
        theme = select_auto_theme()
        show.theme_key = theme.key
        show.effect = theme.default_effect

    def wake_show(self, room_id: str) -> None:
        """Wake a running show so the next frame uses current params immediately."""
        show = self.shows.get(room_id)
        if show is None or not show.running:
            return
        show._wake.set()

    def _options(self) -> dict[str, Any]:
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return {}
        return dict(entries[0].options or {})

    def current_theme(self, show: RoomShow) -> Theme:
        opts = self._options()
        if show.mode == MODE_AUTO:
            return select_auto_theme()
        if show.theme_key == THEME_CUSTOM_COLOR:
            rgb = show.rgb_color or DEFAULT_WARM_WHITE
            return Theme(
                key=THEME_CUSTOM_COLOR,
                name="Custom Color",
                palette=(tuple(int(c) for c in rgb),),  # type: ignore[arg-type]
                default_effect=EFFECT_STATIC,
                manual_only=True,
            )
        theme = resolve_theme(
            show.theme_key,
            custom_themes=opts.get(CONF_CUSTOM_THEMES),
            team_colors=opts.get(CONF_TEAM_COLORS),
        )
        return theme or select_auto_theme()

    def _loop_active(self, show: RoomShow) -> bool:
        return (
            show.running
            and show._task is not None
            and not show._task.done()
        )

    async def start(self, room_id: str, theme_key: str | None = None) -> None:
        show = self.shows.get(room_id)
        if show is None:
            _LOGGER.warning("Unknown room %s", room_id)
            return
        if theme_key:
            show.theme_key = theme_key
            show.mode = MODE_MANUAL

        # Already running: hot-swap params into the live loop (no stop/restore).
        if self._loop_active(show):
            theme = self.current_theme(show)
            if show.mode == MODE_AUTO:
                show.theme_key = theme.key
                show.effect = theme.default_effect
            elif not show.effect:
                show.effect = theme.default_effect
            show._wake.set()
            self._notify()
            return

        lights = show.room.resolve_lights(self.hass)
        show._saved = snapshot_states(self.hass, [li.entity_id for li in lights])
        show.running = True
        show._tick = 0
        show._wake = asyncio.Event()
        theme = self.current_theme(show)
        if show.mode == MODE_AUTO:
            show.theme_key = theme.key
            show.effect = theme.default_effect
        elif not show.effect:
            show.effect = theme.default_effect
        self._notify()
        # Background loop so hass.async_block_till_done() is not stalled.
        show._task = self.hass.async_create_background_task(
            self._show_loop(show), name=f"glowcast_show_{room_id}"
        )

    async def set_color(
        self,
        room_id: str,
        rgb_color: tuple[int, int, int] | list[int],
        brightness: int | None = None,
    ) -> None:
        """Apply a solid RGB color (custom_color theme / static effect).

        From idle: snapshot and start like a show. While running: hot-swap
        into static with this single color (no restore / re-snapshot).
        Always switches mode to manual.
        """
        show = self.shows.get(room_id)
        if show is None:
            _LOGGER.warning("Unknown room %s", room_id)
            return

        r, g, b = (int(rgb_color[0]), int(rgb_color[1]), int(rgb_color[2]))
        show.rgb_color = (
            max(0, min(255, r)),
            max(0, min(255, g)),
            max(0, min(255, b)),
        )
        show.theme_key = THEME_CUSTOM_COLOR
        show.effect = EFFECT_STATIC
        show.mode = MODE_MANUAL
        if brightness is not None:
            show.brightness = max(0, min(100, int(brightness)))

        if self._loop_active(show):
            show._wake.set()
            self._notify()
            return

        lights = show.room.resolve_lights(self.hass)
        show._saved = snapshot_states(self.hass, [li.entity_id for li in lights])
        show.running = True
        show._tick = 0
        show._wake = asyncio.Event()
        self._notify()
        show._task = self.hass.async_create_background_task(
            self._show_loop(show), name=f"glowcast_show_{room_id}"
        )

    async def stop(self, room_id: str) -> None:
        show = self.shows.get(room_id)
        if show is None:
            return
        show.running = False
        show._wake.set()  # unblock sleep so cancel is prompt
        task = show._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        show._task = None
        # Wait for rate-limit window so in-flight BLE/Zigbee updates finish
        await self._settle_before_restore(show)
        async with show._lock:
            await self._restore(show)
        self._notify()

    async def stop_all(self) -> None:
        for rid in list(self.shows):
            if self.shows[rid].running:
                await self.stop(rid)

    async def async_unload(self) -> None:
        await self.stop_all()
        if self._midnight_unsub:
            self._midnight_unsub()
            self._midnight_unsub = None

    def _notify(self) -> None:
        self.hass.bus.async_fire(f"{DOMAIN}_update")

    async def _on_midnight(self, _now=None) -> None:
        """Re-pick auto themes at local midnight."""
        for show in self.shows.values():
            if show.mode == MODE_AUTO:
                self.apply_auto_theme(show)
                if show.running:
                    show._wake.set()
        self._notify()

    def _step_seconds(self, speed: int) -> float:
        # speed 1 = slow (~3s), 10 = fast (~0.25s)
        speed = max(1, min(10, speed))
        return 3.2 - (speed - 1) * (2.95 / 9)

    async def _settle_before_restore(self, show: RoomShow) -> None:
        """Sleep until each light's min interval since last update has elapsed."""
        lights = show.room.resolve_lights(self.hass)
        if not lights:
            return
        now = time.monotonic()
        delay = 0.0
        for light in lights:
            remaining = light.min_interval - (now - light._last_update)
            if remaining > delay:
                delay = remaining
        if delay > 0:
            await asyncio.sleep(delay)

    async def _show_loop(self, show: RoomShow) -> None:
        """Render frames until stopped; sleep is interruptible via show._wake."""
        try:
            while show.running:
                await self._animate_room(show)
                if not show.running:
                    return
                try:
                    await asyncio.wait_for(show._wake.wait(), timeout=_FRAME_SECONDS)
                except asyncio.TimeoutError:
                    pass
                show._wake.clear()
        except asyncio.CancelledError:
            raise

    async def _animate_room(self, show: RoomShow) -> None:
        async with show._lock:
            if not show.running:
                return
            theme = self.current_theme(show)
            effect = show.effect or theme.default_effect
            lights = show.room.resolve_lights(self.hass)
            if not lights:
                return
            now = time.monotonic()
            bright = brightness_pct_to_ha(show.brightness)
            tick = show._tick
            show._tick += 1
            step = self._step_seconds(show.speed)

            # Only advance visual phase every step_seconds / 0.2 ticks roughly
            phase = tick * 0.2 / max(step, 0.05)

            palette = theme.palette
            if not palette:
                return

            for idx, light in enumerate(lights):
                if not show.running:
                    return
                if not light.can_update(now):
                    continue
                color = palette[idx % len(palette)]
                service_data = self._build_service_data(
                    light, color, bright, effect, phase, idx, len(lights), palette
                )
                if service_data is None:
                    continue
                try:
                    await self.hass.services.async_call(
                        "light",
                        SERVICE_TURN_ON,
                        service_data,
                        blocking=False,
                    )
                    light.mark_updated(now)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Failed to update %s", light.entity_id, exc_info=True)

    def _build_service_data(
        self,
        light: LightInfo,
        color: tuple[int, int, int],
        brightness: int,
        effect: str,
        phase: float,
        idx: int,
        count: int,
        palette: tuple[tuple[int, int, int], ...],
    ) -> dict[str, Any] | None:
        data: dict[str, Any] = {"entity_id": light.entity_id}

        # Effect modulation
        if effect == EFFECT_STATIC:
            rgb = color
            bri = brightness
        elif effect == EFFECT_ROTATE:
            shift = int(phase) % len(palette)
            rgb = palette[(idx + shift) % len(palette)]
            bri = brightness
        elif effect == EFFECT_BREATHE:
            # 0.3 .. 1.0 brightness sine
            factor = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(phase * math.pi * 2))
            rgb = color
            bri = max(1, int(brightness * factor))
        elif effect == EFFECT_TWINKLE:
            if random.random() < 0.25:
                rgb = random.choice(palette)
                bri = max(1, int(brightness * random.uniform(0.2, 1.0)))
            else:
                return None  # skip this light this tick
        elif effect == EFFECT_FLICKER:
            rgb = color
            bri = max(1, int(brightness * random.uniform(0.55, 1.0)))
        else:
            rgb = color
            bri = brightness

        if light.capability == CAP_COLOR:
            data["rgb_color"] = list(rgb)
            data["brightness"] = bri
        elif light.capability == CAP_COLOR_TEMP:
            data["color_temp_kelvin"] = rgb_to_color_temp_kelvin(rgb)
            data["brightness"] = bri
        elif light.capability == CAP_BRIGHTNESS:
            data["brightness"] = bri
        elif light.capability == CAP_ONOFF:
            pass  # just turn on
        else:
            data["brightness"] = bri
        return data

    async def _restore(self, show: RoomShow) -> None:
        saved = show._saved or {}
        for eid, snap in saved.items():
            try:
                if snap.get("state") == "off":
                    await self.hass.services.async_call(
                        "light",
                        SERVICE_TURN_OFF,
                        {"entity_id": eid},
                        blocking=True,
                    )
                    continue
                data: dict[str, Any] = {"entity_id": eid}
                if snap.get("brightness") is not None:
                    data["brightness"] = snap["brightness"]
                if snap.get("rgb_color") is not None:
                    data["rgb_color"] = snap["rgb_color"]
                elif snap.get("hs_color") is not None:
                    data["hs_color"] = snap["hs_color"]
                elif snap.get("color_temp_kelvin") is not None:
                    data["color_temp_kelvin"] = snap["color_temp_kelvin"]
                await self.hass.services.async_call(
                    "light",
                    SERVICE_TURN_ON,
                    data,
                    blocking=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Restore failed for %s", eid, exc_info=True)
        show._saved = {}
