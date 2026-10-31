"""GlowCast theme library.

Swap or extend this module (or replace THEMES) without touching animation code.
Each built-in theme has: palette (list of RGB tuples), default effect, and an
optional auto window used by Auto mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from .const import (
    EFFECT_BREATHE,
    EFFECT_FLICKER,
    EFFECT_ROTATE,
    EFFECT_STATIC,
    EFFECT_TWINKLE,
)


@dataclass(frozen=True, slots=True)
class Theme:
    """A named color show theme."""

    key: str
    name: str
    palette: tuple[tuple[int, int, int], ...]
    default_effect: str
    # Optional inclusive date window for Auto mode. For movable holidays,
    # window_fn(year) -> (start, end) is used instead of fixed month/day.
    auto_start: tuple[int, int] | None = None  # (month, day)
    auto_end: tuple[int, int] | None = None
    window_fn: Callable[[int], tuple[date, date]] | None = field(
        default=None, compare=False, hash=False, repr=False
    )
    holiday: bool = False
    manual_only: bool = False


def _easter_date(year: int) -> date:
    """Anonymous Gregorian algorithm (Western Easter)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _easter_window(year: int) -> tuple[date, date]:
    easter = _easter_date(year)
    return easter - timedelta(days=7), easter


def _us_thanksgiving(year: int) -> date:
    """Fourth Thursday of November (US)."""
    # Nov 1 weekday: Mon=0 .. Sun=6
    first = date(year, 11, 1)
    # Thursday = 3
    offset = (3 - first.weekday()) % 7
    first_thu = first + timedelta(days=offset)
    return first_thu + timedelta(weeks=3)


def _thanksgiving_window(year: int) -> tuple[date, date]:
    thanks = _us_thanksgiving(year)
    # Week of Thanksgiving: Mon before through Sun after (or Sun before through Sat)
    start = thanks - timedelta(days=thanks.weekday())  # Monday of that week
    end = start + timedelta(days=6)
    return start, end


def _christmas_window(year: int) -> tuple[date, date]:
    thanks = _us_thanksgiving(year)
    start = thanks + timedelta(days=1)
    end = date(year, 12, 30)
    return start, end


def _new_years_window(year: int) -> tuple[date, date]:
    # Dec 31 of previous calendar year through Jan 1 of year
    # For "is today in window" we evaluate both year and year-1.
    return date(year, 12, 31), date(year + 1, 1, 1)


# Built-in holiday + ambient themes. Teammates can replace THEMES wholesale.
THEMES: dict[str, Theme] = {
    "new_years": Theme(
        key="new_years",
        name="New Year's",
        palette=((255, 215, 0), (192, 192, 192), (255, 255, 255)),
        default_effect=EFFECT_TWINKLE,
        window_fn=_new_years_window,
        holiday=True,
    ),
    "valentines": Theme(
        key="valentines",
        name="Valentine's",
        palette=((220, 20, 60), (255, 105, 180), (255, 250, 250)),
        default_effect=EFFECT_BREATHE,
        auto_start=(2, 7),
        auto_end=(2, 14),
        holiday=True,
    ),
    "st_patricks": Theme(
        key="st_patricks",
        name="St. Patrick's",
        palette=((0, 128, 0), (144, 238, 144), (255, 215, 0)),
        default_effect=EFFECT_ROTATE,
        auto_start=(3, 10),
        auto_end=(3, 17),
        holiday=True,
    ),
    "easter": Theme(
        key="easter",
        name="Easter",
        palette=((255, 182, 193), (230, 190, 255), (152, 255, 152), (255, 255, 153)),
        default_effect=EFFECT_BREATHE,
        window_fn=_easter_window,
        holiday=True,
    ),
    "fourth_of_july": Theme(
        key="fourth_of_july",
        name="Fourth of July",
        palette=((178, 34, 34), (255, 255, 255), (30, 64, 175)),
        default_effect=EFFECT_ROTATE,
        auto_start=(6, 28),
        auto_end=(7, 5),
        holiday=True,
    ),
    "halloween": Theme(
        key="halloween",
        name="Halloween",
        palette=((255, 140, 0), (128, 0, 128), (34, 139, 34)),
        default_effect=EFFECT_FLICKER,
        auto_start=(10, 1),
        auto_end=(10, 31),
        holiday=True,
    ),
    "thanksgiving": Theme(
        key="thanksgiving",
        name="Thanksgiving",
        palette=((255, 191, 0), (204, 85, 0), (139, 0, 0)),
        default_effect=EFFECT_STATIC,
        window_fn=_thanksgiving_window,
        holiday=True,
    ),
    "christmas": Theme(
        key="christmas",
        name="Christmas",
        palette=((200, 16, 46), (0, 132, 61), (255, 200, 150)),
        default_effect=EFFECT_ROTATE,
        window_fn=_christmas_window,
        holiday=True,
    ),
    # Manual / ambient extras
    "aurora": Theme(
        key="aurora",
        name="Aurora",
        palette=((0, 255, 170), (80, 120, 255), (180, 80, 255), (40, 220, 200)),
        default_effect=EFFECT_BREATHE,
        manual_only=True,
    ),
    "ocean": Theme(
        key="ocean",
        name="Ocean",
        palette=((0, 105, 148), (0, 168, 204), (64, 224, 208), (173, 216, 230)),
        default_effect=EFFECT_ROTATE,
        manual_only=True,
    ),
    "sunset": Theme(
        key="sunset",
        name="Sunset",
        palette=((255, 94, 58), (255, 149, 56), (255, 199, 95), (180, 60, 120)),
        default_effect=EFFECT_BREATHE,
        manual_only=True,
    ),
    "candlelight": Theme(
        key="candlelight",
        name="Candlelight",
        palette=((255, 147, 41), (255, 180, 80), (255, 120, 30)),
        default_effect=EFFECT_FLICKER,
        manual_only=True,
    ),
    "party": Theme(
        key="party",
        name="Party",
        palette=((255, 0, 80), (0, 200, 255), (255, 220, 0), (180, 0, 255), (0, 255, 120)),
        default_effect=EFFECT_TWINKLE,
        manual_only=True,
    ),
    "calm": Theme(
        key="calm",
        name="Calm",
        palette=((160, 196, 255), (200, 220, 255), (180, 210, 230)),
        default_effect=EFFECT_BREATHE,
        manual_only=True,
    ),
    "team_colors": Theme(
        key="team_colors",
        name="Team Colors",
        palette=((0, 51, 160), (255, 255, 255)),  # replaced at runtime from options
        default_effect=EFFECT_ROTATE,
        manual_only=True,
    ),
    "custom_color": Theme(
        key="custom_color",
        name="Custom Color",
        palette=((255, 180, 100),),  # replaced at runtime from RoomShow.rgb_color
        default_effect=EFFECT_STATIC,
        manual_only=True,
    ),
}

DEFAULT_THEME_KEY = "calm"


def theme_window_for_year(theme: Theme, year: int) -> tuple[date, date] | None:
    """Return inclusive (start, end) for theme in a given year, if Auto-eligible."""
    if theme.window_fn is not None:
        return theme.window_fn(year)
    if theme.auto_start and theme.auto_end:
        return (
            date(year, theme.auto_start[0], theme.auto_start[1]),
            date(year, theme.auto_end[0], theme.auto_end[1]),
        )
    return None


def theme_active_on(theme: Theme, on: date) -> bool:
    """True if `on` falls inside the theme's auto window (handles year wrap)."""
    if theme.manual_only:
        return False
    for year in (on.year - 1, on.year, on.year + 1):
        window = theme_window_for_year(theme, year)
        if window is None:
            continue
        start, end = window
        if start <= on <= end:
            return True
    return False


def select_auto_theme(on: date | None = None) -> Theme:
    """Pick current holiday theme, else next upcoming holiday, else DEFAULT."""
    today = on or date.today()
    holidays = [t for t in THEMES.values() if t.holiday]

    current = [t for t in holidays if theme_active_on(t, today)]
    if current:
        # Prefer the one whose window ends soonest (most specific "now")
        def _end_distance(t: Theme) -> int:
            for year in (today.year - 1, today.year, today.year + 1):
                w = theme_window_for_year(t, year)
                if w and w[0] <= today <= w[1]:
                    return (w[1] - today).days
            return 999

        current.sort(key=_end_distance)
        return current[0]

    # Next upcoming: soonest future start among holidays
    soonest: tuple[int, Theme] | None = None
    for t in holidays:
        for year in (today.year, today.year + 1):
            w = theme_window_for_year(t, year)
            if w is None:
                continue
            start, _end = w
            if start >= today:
                days = (start - today).days
                if soonest is None or days < soonest[0]:
                    soonest = (days, t)
                break
    if soonest:
        return soonest[1]
    return THEMES[DEFAULT_THEME_KEY]


def resolve_theme(
    key: str,
    *,
    custom_themes: list[dict[str, Any]] | None = None,
    team_colors: list[list[int]] | None = None,
) -> Theme | None:
    """Resolve a theme key from built-ins, team colors override, or custom themes."""
    custom_themes = custom_themes or []
    if key == "team_colors":
        base = THEMES["team_colors"]
        if team_colors and len(team_colors) >= 2:
            palette = tuple(tuple(int(c) for c in rgb[:3]) for rgb in team_colors[:2])  # type: ignore[misc]
            return Theme(
                key=base.key,
                name=base.name,
                palette=palette,  # type: ignore[arg-type]
                default_effect=base.default_effect,
                manual_only=True,
            )
        return base
    if key in THEMES:
        return THEMES[key]
    for custom in custom_themes:
        if custom.get("key") == key or custom.get("name", "").lower().replace(" ", "_") == key:
            colors = custom.get("colors") or custom.get("palette") or []
            palette = tuple(tuple(int(x) for x in c[:3]) for c in colors)  # type: ignore[misc]
            if not palette:
                continue
            return Theme(
                key=str(custom.get("key") or key),
                name=str(custom.get("name") or key),
                palette=palette,  # type: ignore[arg-type]
                default_effect=str(custom.get("effect") or EFFECT_STATIC),
                manual_only=True,
            )
    return None


def all_theme_options(
    custom_themes: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    """Return (key, name) options for selects."""
    opts = [(t.key, t.name) for t in THEMES.values()]
    for custom in custom_themes or []:
        key = str(custom.get("key") or custom.get("name", "custom").lower().replace(" ", "_"))
        name = str(custom.get("name") or key)
        opts.append((key, name))
    return opts


def rgb_to_color_temp_kelvin(rgb: tuple[int, int, int]) -> int:
    """Map an RGB palette color to a rough Kelvin for color-temp-only lights."""
    r, g, b = rgb
    # Warmth heuristic: more red/less blue => warmer
    warmth = (r - b) / 255.0  # -1..1
    # Map to ~2000K (warm) .. 6500K (cool)
    kelvin = int(4250 - warmth * 2250)
    return max(2000, min(6500, kelvin))


def brightness_pct_to_ha(pct: float) -> int:
    """Convert 1-100% to HA brightness 1-255."""
    pct = max(1.0, min(100.0, pct))
    return max(1, min(255, round(pct * 255 / 100)))
