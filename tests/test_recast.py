from types import SimpleNamespace

import pytest

from wowfish.config import ConfigError, load_config
from wowfish.engine import events as ev
from wowfish.engine.state import State
from wowfish.gui.presenter import Presenter
from wowfish.input._win32 import Win32InputDevice
from wowfish.input.keys import VK_CODES, is_supported_key


def test_recast_chord_presses_and_releases_modifiers_in_order(monkeypatch):
    device = object.__new__(Win32InputDevice)
    device._u = SimpleNamespace(MapVirtualKeyW=lambda vk, _: vk)
    sent = []
    device._send = lambda scan, up: sent.append((scan, up))
    monkeypatch.setattr("wowfish.input._win32.time.sleep", lambda _: None)
    device.press_key("Ctrl + Space")
    assert sent == [
        (VK_CODES["ctrl"], False),
        (VK_CODES["space"], False),
        (VK_CODES["space"], True),
        (VK_CODES["ctrl"], True),
    ]
    assert not is_supported_key("ctrl+ctrl+space")
    assert not is_supported_key("ctrl+")


def test_empty_bar_waits_two_seconds_and_retries_failed_cast(world):
    game, engine = world
    game.tick(0)
    game.tick(1.99)
    assert not game.presses
    game.tick(2.01)
    game.tick(3.99)
    assert game.presses == [("space", 2.01)]
    game.tick(4.02)
    assert game.presses[-1] == ("ctrl+space", 4.02)
    game.tick(4.2, visible=True)
    game.tick(100)
    assert len(game.presses) == 2
    assert engine.state is State.WAIT_BITE


def test_existing_or_manually_started_cast_is_not_interrupted(world):
    game, engine = world
    game.tick(0)
    game.tick(1.9, visible=True)
    game.tick(2.1)
    game.tick(80)
    assert not game.presses
    assert engine.stats.casts == 1


def test_manual_cast_during_final_frame_check_cancels_keypress(world):
    game, _engine = world
    game.tick(0)

    def start_during_recheck():
        if game.grabs == 3:
            game.visible = True

    game.on_grab = start_during_recheck
    game.tick(2.01)
    assert game.visible
    assert not game.presses


@pytest.mark.parametrize("condition", ["focused", "moving"])
def test_input_guard_catches_focus_or_wasd_change_during_capture(world, condition):
    game, _engine = world
    game.tick(0)
    game.on_grab = lambda: setattr(game, condition, condition == "moving")
    game.tick(2.01)
    assert not game.presses


def test_bite_is_immediate_and_next_cast_is_fast(world):
    game, engine = world
    game.tick(0, visible=True)
    game.tick(0.2, bites=[game.bite()])
    assert game.presses == [("space", 0.2)]
    assert engine.state is State.INTERACT
    game.tick(0.4, visible=False)
    game.tick(0.6)
    assert engine.stats.catches == 1
    game.tick(0.89)
    assert len(game.presses) == 1
    game.tick(0.91)
    assert game.presses[-1] == ("space", 0.91)


def test_casting_splash_is_ignored_until_armed(world):
    game, _engine = world
    game.tick(0)
    game.tick(0.2, visible=True, fraction=0.98, bites=[game.bite()])
    game.tick(1, bites=[])
    assert not game.presses
    game.tick(1.2, bites=[game.bite()])
    assert game.presses == [("space", 1.2)]


def test_existing_full_bar_listens_without_a_new_cast_delay(world):
    game, _engine = world
    game.tick(0, visible=True, fraction=0.98)
    game.tick(0.2, bites=[game.bite()])
    assert game.presses == [("space", 0.2)]


def test_cancelled_bar_during_bite_processing_does_not_send_space(world):
    game, _engine = world
    game.tick(0, visible=True)

    def cancel_on_recheck():
        if game.grabs == 3:
            game.visible = False

    game.on_grab = cancel_on_recheck
    game.tick(0.2, bites=[game.bite()])
    assert not game.presses


def test_sound_from_previous_cast_cannot_hook_a_new_manual_cast(world):
    game, engine = world
    game.tick(0, visible=True, fraction=0.2)

    def refill_on_recheck():
        if game.grabs == 3:
            game.fraction = 0.98

    game.on_grab = refill_on_recheck
    game.tick(0.2, bites=[game.bite()])
    assert engine.stats.casts == 2
    assert not game.presses


def test_blocked_retry_does_not_count_as_another_failed_cast(world):
    game, engine = world
    game.tick(0)
    game.tick(2.01)
    game.on_grab = lambda: setattr(game, "focused", False)
    game.tick(4.02)
    assert len(game.presses) == 1
    assert engine.stats.misses == 0


def test_sound_without_fishing_bar_never_hooks(world):
    game, engine = world
    game.tick(0, bites=[game.bite()])
    game.tick(1)
    assert not game.presses
    assert engine.stats.bites_heard == 0


def test_short_visual_dropout_does_not_restart_or_count_miss(world):
    game, engine = world
    game.tick(0, visible=True)
    game.tick(1, visible=False)
    game.tick(1.11, visible=True)
    game.tick(4)
    assert not game.presses
    assert engine.stats.misses == 0
    assert engine.stats.casts == 1


def test_expired_or_cancelled_bar_recasts_after_observed_absence(world):
    game, engine = world
    game.tick(0, visible=True)
    game.tick(20, visible=False)
    game.tick(20.2)
    game.tick(21.99)
    assert not game.presses
    game.tick(22.01)
    assert game.presses == [("space", 22.01)]
    assert engine.stats.misses == 1


def test_failed_hook_does_not_cancel_visible_channel(world):
    game, engine = world
    game.tick(0, visible=True)
    game.tick(0.2, bites=[game.bite()])
    game.tick(3.3)
    game.tick(20)
    assert len(game.presses) == 1
    assert engine.state is State.WAIT_BITE


def test_success_flash_after_hook_is_not_a_new_manual_cast(world):
    game, engine = world
    game.tick(0, visible=True, fraction=0.4)
    game.tick(0.2, bites=[game.bite()])
    game.tick(0.4, fraction=1.0)
    assert engine.stats.casts == 1
    assert engine.state is State.INTERACT
    game.tick(0.6, visible=False)
    game.tick(0.8)
    assert engine.stats.catches == 1
    assert engine.stats.misses == 0
    game.tick(1.11)
    assert game.presses[-1] == ("space", 1.11)


def test_unfocused_time_is_not_used_as_absence_evidence(world):
    game, _engine = world
    game.tick(0)
    game.tick(1, focused=False)
    game.tick(60)
    game.tick(61, focused=True)
    game.tick(62.9)
    assert not game.presses
    game.tick(63.01)
    assert game.presses == [("space", 63.01)]


def test_refilled_bar_is_a_new_manual_cycle(world):
    game, engine = world
    game.tick(0, visible=True, fraction=0.2)
    game.tick(1, fraction=0.98)
    assert engine.stats.casts == 2
    assert not game.presses


def test_stop_during_final_frame_check_prevents_keypress(world):
    game, engine = world
    game.tick(0)
    game.on_grab = engine.stop
    game.tick(2.01)
    assert not game.presses


def test_start_failure_is_visible_without_starting_gui(tmp_path, monkeypatch):
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))

    def fail_start(*_args, **_kwargs):
        raise RuntimeError("capture failed")

    presenter = Presenter(build_engine=fail_start)
    presenter.start()
    view = presenter.poll()
    assert not view.running
    assert view.state == "STOPPED"
    assert view.progress.kind == "error"
    assert "capture failed" in view.did


def test_background_error_clears_previous_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    presenter = Presenter()
    presenter.progress = ev.Progress(0, "fishing", "waiting", 0.8)
    presenter._apply(ev.Error(1, "capture failed"))
    presenter._apply(ev.Stopped(1, "error"))
    assert presenter.progress.kind == "error"
    assert presenter.progress.fraction == 0


def test_progress_reports_fixed_movement_countdown(world):
    game, engine = world
    game.tick(0, moving=True)
    game.tick(1, moving=False)
    game.tick(1.5)
    progress = [e for e in list(engine.events.queue) if isinstance(e, ev.Progress)][-1]
    assert progress.kind == "movement"
    assert progress.remaining_s == pytest.approx(1.5)
    assert progress.fraction == pytest.approx(0.75)


def test_random_transition_sequences_preserve_input_guards(world):
    import random

    game, engine = world
    randomizer = random.Random(3548)
    now = 0.0
    for _ in range(3000):
        now += randomizer.uniform(0.03, 0.7)
        game.tick(
            now,
            visible=randomizer.random() < 0.45,
            focused=randomizer.random() < 0.94,
            moving=randomizer.random() < 0.08,
            bites=[game.bite()] if randomizer.random() < 0.12 else [],
        )
        while not engine.events.empty():
            event = engine.events.get_nowait()
            if isinstance(event, ev.KeyPressed):
                assert game.focused and not game.moving
                assert now >= engine._movement_until
                assert game.visible == (event.why == "bite")
        assert engine.stats.catches <= engine.stats.bites_heard


def test_settings_migrate_old_ranges_and_validate_new_values(tmp_path, monkeypatch):
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    legacy = tmp_path / "legacy.toml"
    legacy.write_text("[run]\nmovement_pause_min_s=1\nmovement_pause_max_s=5\ncast_timeout_s=30\n")
    cfg, warnings = load_config(legacy)
    assert not warnings
    assert cfg.run.movement_pause_s == 2
    assert cfg.run.recast_delay_s == 2
    presenter = Presenter()
    assert presenter.save_recast_delay("2.5") == 2.5
    presenter.save_movement_pause(False)
    assert not presenter.cfg.run.movement_pause_enabled
    for value in ("nan", "0", "31", "wrong", "inf"):
        with pytest.raises(ValueError):
            presenter.save_recast_delay(value)
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[run]\nmovement_pause_s = nan\n")
    with pytest.raises(ConfigError):
        load_config(invalid)
