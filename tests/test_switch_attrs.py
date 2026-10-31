"""Show switch rgb_color attribute only present while active."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.glowcast.rooms import Room
from custom_components.glowcast.show import RoomShow
from custom_components.glowcast.switch import GlowCastShowSwitch


def _switch():
    room = Room(room_id="den", name="Den", kind="custom", entity_ids=["light.den"])
    show = RoomShow(room)
    manager = MagicMock()
    manager.ensure_room.return_value = show
    return GlowCastShowSwitch(manager, room), show


def test_rgb_color_omitted_when_off():
    sw, show = _switch()
    show.rgb_color = (10, 20, 30)
    show.running = False
    assert "rgb_color" not in (sw.extra_state_attributes or {})


def test_rgb_color_present_when_running():
    sw, show = _switch()
    show.rgb_color = (10, 20, 30)
    show.running = True
    assert sw.extra_state_attributes == {"rgb_color": [10, 20, 30]}
