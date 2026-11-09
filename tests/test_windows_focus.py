from types import SimpleNamespace

import pytest

from wowfish.capture import WindowRef
from wowfish.input._win32 import Win32InputDevice


def test_missing_foreground_window_is_unfocused():
    device = object.__new__(Win32InputDevice)
    device._u = SimpleNamespace(GetForegroundWindow=lambda: None)
    game = WindowRef("World of Warcraft", 1, 0, 0, 1920, 1080, handle=42)
    assert device.is_game_focused(game) is False
    device._u = SimpleNamespace(GetForegroundWindow=lambda: 42)
    assert device.is_game_focused(game) is True


def test_key_is_released_when_sleep_is_interrupted(monkeypatch):
    device = object.__new__(Win32InputDevice)
    device._u = SimpleNamespace(MapVirtualKeyW=lambda *_: 0x39)
    sent = []
    device._send = lambda scan, up: sent.append((scan, up))

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("wowfish.input._win32.time.sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        device.press_key("space")
    assert sent == [(0x39, False), (0x39, True)]
