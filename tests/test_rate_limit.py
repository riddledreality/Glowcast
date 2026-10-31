"""Per-light rate limiting."""

from __future__ import annotations

import time

from custom_components.glowcast.lights import LightInfo, is_slow_light


def test_rate_limit_blocks_then_allows():
    light = LightInfo("light.slow", None, "color", True, min_interval=1.0)
    now = time.monotonic()
    assert light.can_update(now)
    light.mark_updated(now)
    assert not light.can_update(now + 0.2)
    assert light.can_update(now + 1.01)


def test_fast_vs_slow_intervals():
    slow = LightInfo("light.a", None, "color", True, 1.0)
    fast = LightInfo("light.b", None, "color", False, 0.3)
    t0 = time.monotonic()
    slow.mark_updated(t0)
    fast.mark_updated(t0)
    assert not slow.can_update(t0 + 0.5)
    assert fast.can_update(t0 + 0.5)


def test_slow_heuristic_govee_name():
    assert is_slow_light("light.govee_lamp", {}, None) is True
    assert is_slow_light("light.kitchen_ceiling", {}, None) is False


def test_slow_explicit_list():
    opts = {"slow_lights": ["light.custom_ble"]}
    assert is_slow_light("light.custom_ble", opts, None) is True
