from wowfish.capture import WindowRef
from wowfish.engine.state import State


def test_window_move_resize_refreshes_capture_and_adopts_visible_bar(world):
    game, engine = world
    moved = WindowRef("World of Warcraft", 1, 275, 129, 2025, 1139, handle=42)
    opened = []
    engine._find_window = lambda _: moved
    engine.capturer.open = opened.append
    game.tick(0, visible=True)
    assert engine.window == moved
    assert opened == [moved]
    assert engine.state is State.WAIT_BITE
    assert not game.presses


def test_missing_window_pauses_without_capture_or_input(world):
    game, engine = world
    engine._find_window = lambda _: None
    game.tick(0)
    game.tick(20)
    assert engine.state is State.PAUSED
    assert game.grabs == 0
    assert not game.presses


def test_window_change_during_final_countdown_is_refreshed_before_input(world):
    game, engine = world
    old = engine.window
    new = WindowRef(old.title, old.pid, 300, 200, 500, 300, handle=old.handle)
    calls = []

    def find(_):
        calls.append(game.now)
        # First look at t=2 is old; the final action check sees the new position.
        if game.now >= 2 and calls.count(game.now) > 1:
            return new
        return old

    engine._find_window = find
    engine.capturer.open = lambda window: setattr(game, "visible", window == new)
    game.tick(0)
    game.tick(2.01)
    assert engine.window == new
    assert not game.presses


def test_game_restart_stops_instead_of_listening_to_previous_process(world):
    game, engine = world
    old = engine.window
    engine._find_window = lambda _: WindowRef(old.title, 999, 0, 0, 200, 100, handle=99)
    game.tick(0)
    assert engine.state is State.STOPPED
    assert not game.presses
