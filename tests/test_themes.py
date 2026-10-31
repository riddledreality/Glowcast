"""Theme auto-selection and Easter / Thanksgiving helpers."""

from __future__ import annotations

from datetime import date

from custom_components.glowcast.themes import (
    THEMES,
    _easter_date,
    _us_thanksgiving,
    resolve_theme,
    rgb_to_color_temp_kelvin,
    select_auto_theme,
    theme_active_on,
)


def test_easter_known_dates():
    assert _easter_date(2025) == date(2025, 4, 20)
    assert _easter_date(2026) == date(2026, 4, 5)
    assert _easter_date(2024) == date(2024, 3, 31)


def test_thanksgiving_us():
    assert _us_thanksgiving(2025) == date(2025, 11, 27)
    assert _us_thanksgiving(2026) == date(2026, 11, 26)


def test_halloween_active_all_october():
    assert theme_active_on(THEMES["halloween"], date(2026, 10, 1))
    assert theme_active_on(THEMES["halloween"], date(2026, 10, 31))
    assert not theme_active_on(THEMES["halloween"], date(2026, 11, 1))


def test_auto_picks_current_holiday():
    theme = select_auto_theme(date(2026, 10, 15))
    assert theme.key == "halloween"


def test_auto_picks_valentines_window():
    assert select_auto_theme(date(2026, 2, 10)).key == "valentines"


def test_auto_next_holiday_when_none_current():
    # Mid-January: next is Valentine's
    theme = select_auto_theme(date(2026, 1, 15))
    assert theme.key == "valentines"


def test_new_years_wraps():
    assert theme_active_on(THEMES["new_years"], date(2025, 12, 31))
    assert theme_active_on(THEMES["new_years"], date(2026, 1, 1))
    assert not theme_active_on(THEMES["new_years"], date(2026, 1, 2))


def test_easter_week_before():
    easter = _easter_date(2026)  # Apr 5
    assert theme_active_on(THEMES["easter"], easter)
    assert theme_active_on(THEMES["easter"], date(2026, 3, 29))
    assert not theme_active_on(THEMES["easter"], date(2026, 3, 28))


def test_christmas_after_thanksgiving():
    # 2026 Thanksgiving Nov 26 → Christmas window Nov 27 – Dec 30
    assert theme_active_on(THEMES["christmas"], date(2026, 11, 27))
    assert theme_active_on(THEMES["christmas"], date(2026, 12, 25))
    assert not theme_active_on(THEMES["christmas"], date(2026, 12, 31))


def test_manual_themes_not_auto():
    assert not theme_active_on(THEMES["aurora"], date(2026, 6, 1))


def test_resolve_custom_theme():
    custom = [
        {
            "key": "neon",
            "name": "Neon",
            "colors": [[255, 0, 255], [0, 255, 255]],
            "effect": "twinkle",
        }
    ]
    t = resolve_theme("neon", custom_themes=custom)
    assert t is not None
    assert t.name == "Neon"
    assert len(t.palette) == 2
    assert t.default_effect == "twinkle"


def test_team_colors_override():
    t = resolve_theme("team_colors", team_colors=[[255, 0, 0], [0, 0, 255]])
    assert t is not None
    assert t.palette[0] == (255, 0, 0)
    assert t.palette[1] == (0, 0, 255)


def test_color_temp_mapping_warm_vs_cool():
    warm = rgb_to_color_temp_kelvin((255, 100, 20))
    cool = rgb_to_color_temp_kelvin((100, 150, 255))
    assert warm < cool
    assert 2000 <= warm <= 6500
    assert 2000 <= cool <= 6500


def test_effects_v1_no_chase():
    from custom_components.glowcast.const import EFFECTS

    assert "chase" not in EFFECTS
    assert set(EFFECTS) == {"static", "rotate", "breathe", "twinkle", "flicker"}
