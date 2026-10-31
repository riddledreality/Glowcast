"""Constants for GlowCast."""

from __future__ import annotations

DOMAIN = "glowcast"
CONF_EXCLUDED_LIGHTS = "excluded_lights"
CONF_SLOW_LIGHTS = "slow_lights"
CONF_SLOW_MIN_INTERVAL = "slow_min_interval"
CONF_FAST_MIN_INTERVAL = "fast_min_interval"
CONF_CUSTOM_THEMES = "custom_themes"
CONF_TEAM_COLORS = "team_colors"
CONF_CUSTOM_GROUPS = "custom_groups"

# Minimum interval between commands per light (seconds).
DEFAULT_SLOW_MIN_INTERVAL = 1.0  # Bluetooth / Govee
DEFAULT_FAST_MIN_INTERVAL = 0.5  # everything else (floor)

# Time budgets — never block the event loop unboundedly.
RESTORE_LIGHT_TIMEOUT = 2.0
SERVICE_CALL_TIMEOUT = 3.0
TASK_CANCEL_TIMEOUT = 5.0
SETTLE_TIMEOUT = 2.0

DEFAULT_WARM_WHITE = (255, 180, 100)

ATTR_ROOM = "room"
ATTR_THEME = "theme"
ATTR_RGB_COLOR = "rgb_color"
ATTR_BRIGHTNESS = "brightness"
ATTR_EMERGENCY = "emergency"

SERVICE_START_THEME = "start_theme"
SERVICE_STOP = "stop"
SERVICE_STOP_ALL = "stop_all"
SERVICE_SET_COLOR = "set_color"

MODE_AUTO = "auto"
MODE_MANUAL = "manual"

EFFECT_STATIC = "static"
EFFECT_ROTATE = "rotate"
EFFECT_BREATHE = "breathe"
EFFECT_TWINKLE = "twinkle"
EFFECT_FLICKER = "flicker"

EFFECTS = (
    EFFECT_STATIC,
    EFFECT_ROTATE,
    EFFECT_BREATHE,
    EFFECT_TWINKLE,
    EFFECT_FLICKER,
)

ROOM_ALL = "all_lights"
ROOM_ALL_NAME = "All lights"

THEME_CUSTOM_COLOR = "custom_color"

KEY_THEME = "theme"
KEY_SHOW = "show"
KEY_SPEED = "speed"
KEY_BRIGHTNESS = "brightness"
KEY_MODE = "mode"
KEY_EFFECT = "effect"
KEY_COLOR = "color"
KEY_ENABLED = "enabled"

PLATFORMS = ("select", "switch", "number", "light")
