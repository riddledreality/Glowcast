"""Capability detection and service-data shaping."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.glowcast.lights import (
    CAP_BRIGHTNESS,
    CAP_COLOR,
    CAP_COLOR_TEMP,
    CAP_ONOFF,
    LightInfo,
    detect_capability,
)
from custom_components.glowcast.show import ShowManager
from custom_components.glowcast.themes import THEMES


def _state(attrs: dict):
    return SimpleNamespace(attributes=attrs, entity_id="light.test", state="on")


def test_detect_rgb():
    assert detect_capability(_state({"supported_color_modes": ["rgb", "brightness"]})) == CAP_COLOR


def test_detect_hs_xy():
    assert detect_capability(_state({"supported_color_modes": {"hs", "xy"}})) == CAP_COLOR


def test_detect_color_temp():
    assert detect_capability(_state({"supported_color_modes": ["color_temp"]})) == CAP_COLOR_TEMP


def test_detect_brightness_only():
    assert detect_capability(_state({"supported_color_modes": ["brightness"]})) == CAP_BRIGHTNESS


def test_detect_onoff():
    assert detect_capability(_state({"supported_color_modes": ["onoff"]})) == CAP_ONOFF
    assert detect_capability(_state({})) == CAP_ONOFF


def test_detect_legacy_features():
    # SUPPORT_COLOR = 16
    assert detect_capability(_state({"supported_features": 16})) == CAP_COLOR
    assert detect_capability(_state({"supported_features": 2})) == CAP_COLOR_TEMP
    assert detect_capability(_state({"supported_features": 1})) == CAP_BRIGHTNESS


def test_build_service_data_by_capability():
    mgr = ShowManager.__new__(ShowManager)
    palette = THEMES["halloween"].palette
    color = palette[0]

    rgb_light = LightInfo("light.rgb", None, CAP_COLOR, False, 0.3)
    data = ShowManager._build_service_data(
        mgr, rgb_light, color, 200, "static", 0.0, 0, 1, palette
    )
    assert data["rgb_color"] == list(color)
    assert data["brightness"] == 200

    ct_light = LightInfo("light.ct", None, CAP_COLOR_TEMP, False, 0.3)
    data = ShowManager._build_service_data(
        mgr, ct_light, color, 200, "static", 0.0, 0, 1, palette
    )
    assert "color_temp_kelvin" in data
    assert data["brightness"] == 200

    bri_light = LightInfo("light.bri", None, CAP_BRIGHTNESS, False, 0.3)
    data = ShowManager._build_service_data(
        mgr, bri_light, color, 180, "static", 0.0, 0, 1, palette
    )
    assert data == {"entity_id": "light.bri", "brightness": 180}

    onoff = LightInfo("light.basic", None, CAP_ONOFF, False, 0.3)
    data = ShowManager._build_service_data(
        mgr, onoff, color, 200, "static", 0.0, 0, 1, palette
    )
    assert data == {"entity_id": "light.basic"}
