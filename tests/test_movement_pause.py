from types import SimpleNamespace

from wowfish.engine import events as ev
from wowfish.engine.state import State
from wowfish.gui.presenter import Presenter
from wowfish.input._win32 import Win32InputDevice


def test_held_wasd_then_exact_two_seconds_after_release(world):
    game, engine = world
    game.tick(0, visible=True)
    game.tick(1, moving=True)
    game.tick(30)
    assert engine.state is State.PAUSED
    game.tick(30.2, moving=False)
    game.tick(32.19)
    assert engine.state is State.PAUSED
    game.tick(32.21)
    assert engine.state is State.WAIT_BITE
    assert game.presses == []


def test_absence_and_movement_countdowns_run_together(world):
    game, _engine = world
    game.tick(0, visible=True)
    game.tick(1, visible=False, moving=True)
    game.tick(3, moving=False)
    game.tick(4.99)
    assert not game.presses
    game.tick(5.01)
    assert game.presses == [("space", 5.01)]


def test_new_movement_restarts_fixed_cooldown(world):
    game, _engine = world
    game.tick(0, moving=True)
    game.tick(1, moving=False)
    game.tick(2.9, moving=True)
    game.tick(4, moving=False)
    game.tick(5.99)
    assert not game.presses
    game.tick(6.01)
    assert len(game.presses) == 1


def test_manual_cast_is_observed_during_wasd_cooldown(world):
    game, engine = world
    game.tick(0, moving=True)
    game.tick(1, moving=False)
    previous_grabs = game.grabs
    game.tick(2, visible=True)
    assert game.grabs > previous_grabs
    game.tick(3.01)
    assert engine.state is State.WAIT_BITE
    assert not game.presses


def test_stopped_bot_never_wakes_up(world):
    game, engine = world
    game.tick(0, moving=True)
    engine.stop()
    game.tick(30, moving=False)
    game.tick(60)
    assert engine.state is State.STOPPED
    assert not game.presses


def test_unfocused_wasd_is_not_observed(world):
    game, engine = world
    game.tick(0, focused=False, moving=True)
    game.tick(10)
    assert game.polls == 0
    assert engine.state is State.PAUSED
    assert not game.presses


def test_movement_pause_can_be_disabled(world):
    game, _engine = world
    game.configure(movement_pause_enabled=False)
    game.tick(0, moving=True)
    game.tick(2.01)
    assert game.presses == [("space", 2.01)]
    assert game.polls == 0


def test_pause_reason_changes_preserve_presenter_resume_state(tmp_path, monkeypatch):
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    presenter = Presenter()
    presenter._apply(ev.StateChanged(0, State.WAIT_BITE, "Waiting for bite", "Listen"))
    presenter._apply(ev.Paused(1, "WASD: moving"))
    presenter._apply(ev.Paused(2, "World of Warcraft is not focused"))
    presenter._apply(ev.Resumed(3, State.WAIT_BITE))
    assert (presenter.state, presenter.did) == ("WAIT_BITE", "Waiting for bite")


def test_windows_movement_reads_current_high_bit_only():
    device = object.__new__(Win32InputDevice)
    device._u = SimpleNamespace(GetAsyncKeyState=lambda vk: 0x8000 if vk == ord("A") else 0)
    assert device.movement_keys_down()
    device._u = SimpleNamespace(GetAsyncKeyState=lambda _: 1)
    assert not device.movement_keys_down()  # A past press is not a currently held key.


def test_mouse_buttons_do_not_trigger_movement_pause():
    device = object.__new__(Win32InputDevice)
    mouse_buttons = {0x01, 0x02, 0x04, 0x05, 0x06}
    device._u = SimpleNamespace(
        GetAsyncKeyState=lambda vk: 0x8000 if vk in mouse_buttons else 0,
    )
    assert not device.movement_keys_down()
