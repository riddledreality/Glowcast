"""Per-light rate limiting (LightRateBook + heuristics)."""

from __future__ import annotations

import time

import pytest

from custom_components.glowcast.const import DEFAULT_FAST_MIN_INTERVAL, DEFAULT_SLOW_MIN_INTERVAL
from custom_components.glowcast.lights import LightRateBook, is_slow_light


def test_default_intervals():
    assert DEFAULT_FAST_MIN_INTERVAL >= 0.5
    assert DEFAULT_SLOW_MIN_INTERVAL >= 1.0


def test_rate_book_blocks_then_allows():
    book = LightRateBook()
    now = time.monotonic()
    assert book.can_send("light.a", 1.0, now)
    book.begin("light.a")
    assert not book.can_send("light.a", 1.0, now)
    book.end("light.a", success=True, min_interval=1.0, now=now)
    assert not book.can_send("light.a", 1.0, now + 0.2)
    assert book.can_send("light.a", 1.0, now + 1.01)


def test_rate_book_backoff_on_error():
    book = LightRateBook()
    now = time.monotonic()
    book.begin("light.a")
    book.end("light.a", success=False, min_interval=0.5, now=now)
    assert book.backoff_until["light.a"] > now
    assert not book.can_send("light.a", 0.5, now + 0.1)


def test_settle_delay_uses_last_sent():
    book = LightRateBook()
    now = time.monotonic()
    book.last_sent["light.a"] = now
    delay = book.settle_delay(["light.a"], {"light.a": 1.0}, now=now)
    assert delay == pytest.approx(1.0, abs=0.01)


def test_slow_heuristic_govee_name():
    assert is_slow_light("light.govee_lamp", {}, None) is True
    assert is_slow_light("light.kitchen_ceiling", {}, None) is False


def test_slow_explicit_list():
    opts = {"slow_lights": ["light.custom_ble"]}
    assert is_slow_light("light.custom_ble", opts, None) is True
