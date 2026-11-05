"""Animation / show engine for GlowCast."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from typing import Any

from homeassistant.const import (
    EVENT_STATE_CHANGED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
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
    RESTORE_LIGHT_TIMEOUT,
    SERVICE_CALL_TIMEOUT,
    SETTLE_TIMEOUT,
    TASK_CANCEL_TIMEOUT,
    THEME_CUSTOM_COLOR,
)
from .lights import (
    CAP_BRIGHTNESS,
    CAP_COLOR,
    CAP_COLOR_TEMP,
    CAP_ONOFF,
    LightInfo,
    LightRateBook,
    snapshot_states,
)
from .rooms import Room
from .themes import (
    Theme,
    brightness_pct_to_ha,
    resolve_theme,
    rgb_to_color_temp_kelvin,
    select_auto_theme,
)

_LOGGER = logging.getLogger(__name__)

# Frame cadence. Cancellation lands on the sleep between frames.
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
        # Single lock serializes start / stop / set_color for this room.
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._saved: dict[str, dict[str, Any]] = {}
        self._tick = 0
        self._restoring = False
        # Lights we have successfully commanded on this show run.
        self._commanded_on: set[str] = set()

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
        self.enabled: bool = True
        # Survives collect_lights() rebuilding LightInfo every frame.
        self.rate = LightRateBook()
        self._midnight_unsub = async_track_time_change(
            hass, self._on_midnight, hour=0, minute=0, second=0
        )
        self._state_unsub = hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._on_state_changed
        )

    def ensure_room(self, room: Room) -> RoomShow:
        if room.room_id not in self.shows:
            show = RoomShow(room)
            self.shows[room.room_id] = show
            self.apply_auto_theme(show)
        else:
            self.shows[room.room_id].room = room
        return self.shows[room.room_id]

    def sync_rooms(self, rooms: dict[str, Room]) -> None:
        for room in rooms.values():
            self.ensure_room(room)
        for rid in list(self.shows):
            if rid not in rooms:
                show = self.shows.pop(rid)
                if show.running or show._task is not None:
                    self.hass.async_create_task(self.stop(rid))

    def apply_auto_theme(self, show: RoomShow) -> None:
        if show.mode != MODE_AUTO:
            return
        theme = select_auto_theme()
        show.theme_key = theme.key
        show.effect = theme.default_effect

    def wake_show(self, room_id: str) -> None:
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
        return show.running and show._task is not None and not show._task.done()

    def _active_task_count(self) -> int:
        return sum(
            1 for s in self.shows.values() if s._task is not None and not s._task.done()
        )

    async def _cancel_task(self, show: RoomShow, timeout: float = TASK_CANCEL_TIMEOUT) -> None:
        task = show._task
        show._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Show task for %s did not finish within %.1fs after cancel",
                show.room.room_id,
                timeout,
            )
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Show task for %s raised during cancel", show.room.room_id, exc_info=True
            )

    async def _stop_locked(self, show: RoomShow, *, restore: bool) -> None:
        """Cancel the room task; optionally restore snapshot. Caller holds show._lock."""
        show.running = False
        show._wake.set()
        await self._cancel_task(show)

        if restore and show._saved:
            try:
                await asyncio.wait_for(
                    self._settle_before_restore(show), timeout=SETTLE_TIMEOUT
                )
            except asyncio.TimeoutError:
                _LOGGER.debug("Settle timed out for %s", show.room.room_id)
            show._restoring = True
            try:
                await self._restore(show)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Restore failed for room %s", show.room.room_id)
                show._saved = {}
            finally:
                show._restoring = False
        elif not restore:
            # Theme swap / emergency: keep snapshot for a later restoring stop,
            # unless emergency cleared it already.
            pass

        show._commanded_on.clear()
        self._notify()

    async def start(self, room_id: str, theme_key: str | None = None) -> None:
        if not self.enabled:
            _LOGGER.info("GlowCast disabled — refusing start for %s", room_id)
            return
        show = self.shows.get(room_id)
        if show is None:
            _LOGGER.warning("Unknown room %s", room_id)
            return

        async with show._lock:
            if theme_key:
                show.theme_key = theme_key
                show.mode = MODE_MANUAL

            theme = self.current_theme(show)
            if show.mode == MODE_AUTO:
                show.theme_key = theme.key
                show.effect = theme.default_effect
            elif not show.effect:
                show.effect = theme.default_effect

            # Spec: if a task is running, stop without restore (keep pre-show snapshot).
            if self._loop_active(show) or (show._task is not None and not show._task.done()):
                await self._stop_locked(show, restore=False)

            # Snapshot only if none exists — theme swaps must not overwrite pre-show state.
            if not show._saved:
                lights = show.room.resolve_lights(self.hass)
                show._saved = snapshot_states(
                    self.hass, [li.entity_id for li in lights]
                )

            show.running = True
            show._tick = 0
            show._commanded_on = set()
            show._wake = asyncio.Event()
            show._task = self.hass.async_create_background_task(
                self._show_loop(show), name=f"glowcast_show_{room_id}"
            )
            self._notify()

    async def set_color(
        self,
        room_id: str,
        rgb_color: tuple[int, int, int] | list[int],
        brightness: int | None = None,
    ) -> None:
        if not self.enabled:
            _LOGGER.info("GlowCast disabled — refusing set_color for %s", room_id)
            return
        show = self.shows.get(room_id)
        if show is None:
            _LOGGER.warning("Unknown room %s", room_id)
            return

        async with show._lock:
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

            if self._loop_active(show) or (show._task is not None and not show._task.done()):
                await self._stop_locked(show, restore=False)

            if not show._saved:
                lights = show.room.resolve_lights(self.hass)
                show._saved = snapshot_states(
                    self.hass, [li.entity_id for li in lights]
                )

            show.running = True
            show._tick = 0
            show._commanded_on = set()
            show._wake = asyncio.Event()
            show._task = self.hass.async_create_background_task(
                self._show_loop(show), name=f"glowcast_show_{room_id}"
            )
            self._notify()

    async def stop(self, room_id: str, *, restore: bool = True) -> None:
        show = self.shows.get(room_id)
        if show is None:
            return
        async with show._lock:
            await self._stop_locked(show, restore=restore)

    async def emergency_stop(self, room_id: str | None = None) -> None:
        """Cancel immediately and discard snapshot — no restore."""
        if room_id:
            show = self.shows.get(room_id)
            if show is None:
                return
            async with show._lock:
                show._saved = {}
                await self._stop_locked(show, restore=False)
            return
        await self.stop_all(restore=False, clear_snapshot=True)

    async def stop_all(
        self, *, restore: bool = True, clear_snapshot: bool = False
    ) -> None:
        targets = [
            rid
            for rid, show in list(self.shows.items())
            if show.running or (show._task is not None and not show._task.done()) or (restore and show._saved)
        ]
        # Always consider running/task rooms; for restore=False emergency, just those.
        if not restore:
            targets = [
                rid
                for rid, show in list(self.shows.items())
                if show.running or (show._task is not None and not show._task.done())
            ]
        if clear_snapshot:
            for rid in targets:
                show = self.shows.get(rid)
                if show:
                    show._saved = {}
        if not targets:
            return
        await asyncio.gather(
            *(self.stop(rid, restore=restore and not clear_snapshot) for rid in targets),
            return_exceptions=True,
        )

    async def async_unload(self) -> None:
        for show in self.shows.values():
            show.running = False
            show._wake.set()
        await asyncio.gather(
            *(self._cancel_task(s) for s in list(self.shows.values())),
            return_exceptions=True,
        )
        for show in list(self.shows.values()):
            if show._saved:
                try:
                    show._restoring = True
                    await self._restore(show)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "Unload restore failed for %s", show.room.room_id, exc_info=True
                    )
                    show._saved = {}
                finally:
                    show._restoring = False
        if self._midnight_unsub:
            self._midnight_unsub()
            self._midnight_unsub = None
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None

    def _notify(self) -> None:
        self.hass.bus.async_fire(f"{DOMAIN}_update")

    async def _on_midnight(self, _now=None) -> None:
        for show in self.shows.values():
            if show.mode == MODE_AUTO:
                self.apply_auto_theme(show)
                if show.running:
                    show._wake.set()
        self._notify()

    @callback
    def _on_state_changed(self, event: Event) -> None:
        """If the user turns off a light mid-show, stop that room's show."""
        eid = event.data.get("entity_id")
        if not eid or not str(eid).startswith("light."):
            return
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if new is None or old is None:
            return
        if new.state != STATE_OFF or old.state != STATE_ON:
            return
        for show in self.shows.values():
            if not show.running or show._restoring:
                continue
            if eid not in show._commanded_on:
                continue
            _LOGGER.info(
                "Light %s turned off during show in %s — stopping show",
                eid,
                show.room.room_id,
            )
            self.hass.async_create_task(self.stop(show.room.room_id, restore=True))
            return

    def _step_seconds(self, speed: int) -> float:
        speed = max(1, min(10, speed))
        return 3.2 - (speed - 1) * (2.95 / 9)

    async def _settle_before_restore(self, show: RoomShow) -> None:
        """Wait using persisted last-sent times (not freshly rebuilt LightInfo)."""
        lights = show.room.resolve_lights(self.hass)
        if not lights:
            return
        intervals = {li.entity_id: li.min_interval for li in lights}
        delay = self.rate.settle_delay([li.entity_id for li in lights], intervals)
        if delay > 0:
            await asyncio.sleep(min(delay, SETTLE_TIMEOUT))

    async def _show_loop(self, show: RoomShow) -> None:
        try:
            while show.running and self.enabled:
                await self._animate_room(show)
                if not show.running or not self.enabled:
                    break
                try:
                    await asyncio.wait_for(show._wake.wait(), timeout=_FRAME_SECONDS)
                except asyncio.TimeoutError:
                    pass
                show._wake.clear()
            # Kill switch flipped mid-loop: stop with restore.
            if not self.enabled and show.running:
                show.running = False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Show loop crashed for %s", show.room.room_id)
            show.running = False

    async def _animate_room(self, show: RoomShow) -> None:
        if not show.running or not self.enabled:
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
        phase = tick * 0.2 / max(step, 0.05)
        palette = theme.palette
        if not palette:
            return

        for idx, light in enumerate(lights):
            if not show.running or not self.enabled:
                return
            if not self.rate.can_send(light.entity_id, light.min_interval, now):
                continue
            state = self.hass.states.get(light.entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                self.rate.end(
                    light.entity_id, success=False, min_interval=light.min_interval, now=now
                )
                continue
            # User turned this light off after we had commanded it on → stop show.
            if (
                light.entity_id in show._commanded_on
                and state.state == STATE_OFF
                and not show._restoring
            ):
                show.running = False
                self.hass.async_create_task(self.stop(show.room.room_id, restore=True))
                return
            color = palette[idx % len(palette)]
            service_data = self._build_service_data(
                light, color, bright, effect, phase, idx, len(lights), palette
            )
            if service_data is None:
                continue
            self.rate.begin(light.entity_id)
            try:
                await asyncio.wait_for(
                    self.hass.services.async_call(
                        "light",
                        SERVICE_TURN_ON,
                        service_data,
                        blocking=True,
                    ),
                    timeout=SERVICE_CALL_TIMEOUT,
                )
                self.rate.end(
                    light.entity_id, success=True, min_interval=light.min_interval, now=now
                )
                show._commanded_on.add(light.entity_id)
            except asyncio.TimeoutError:
                _LOGGER.debug("Light call timed out for %s — backing off", light.entity_id)
                self.rate.end(
                    light.entity_id, success=False, min_interval=light.min_interval, now=now
                )
            except asyncio.CancelledError:
                self.rate.end(
                    light.entity_id, success=False, min_interval=light.min_interval, now=now
                )
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to update %s", light.entity_id, exc_info=True)
                self.rate.end(
                    light.entity_id, success=False, min_interval=light.min_interval, now=now
                )

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

        if effect == EFFECT_STATIC:
            rgb = color
            bri = brightness
        elif effect == EFFECT_ROTATE:
            shift = int(phase) % len(palette)
            rgb = palette[(idx + shift) % len(palette)]
            bri = brightness
        elif effect == EFFECT_BREATHE:
            factor = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(phase * math.pi * 2))
            rgb = color
            bri = max(1, int(brightness * factor))
        elif effect == EFFECT_TWINKLE:
            if random.random() < 0.25:
                rgb = random.choice(palette)
                bri = max(1, int(brightness * random.uniform(0.2, 1.0)))
            else:
                return None
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
            pass
        else:
            data["brightness"] = bri
        return data

    async def _restore_one(self, eid: str, snap: dict[str, Any]) -> None:
        if snap.get("state") == "off":
            await self.hass.services.async_call(
                "light",
                SERVICE_TURN_OFF,
                {"entity_id": eid},
                blocking=True,
            )
            return
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

    async def _restore(self, show: RoomShow) -> None:
        """Restore prior states with per-light 2s timeouts; skip unavailable."""
        saved = show._saved or {}
        show._saved = {}
        for eid, snap in saved.items():
            state = self.hass.states.get(eid)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                _LOGGER.debug("Skipping restore for unavailable light %s", eid)
                continue
            try:
                await asyncio.wait_for(
                    self._restore_one(eid, snap),
                    timeout=RESTORE_LIGHT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Restore timed out after %.1fs for %s — skipping",
                    RESTORE_LIGHT_TIMEOUT,
                    eid,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Restore failed for %s", eid, exc_info=True)
