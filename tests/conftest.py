from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from wowfish.capture import WindowRef
from wowfish.config import AudioConfig, Config, GameConfig, VisionConfig
from wowfish.engine.engine import FishingEngine
from wowfish.vision.cast_bar import BarObservation


@pytest.fixture
def world():
    game = SimpleNamespace(
        now=0.0,
        focused=True,
        moving=False,
        visible=False,
        fraction=0.8,
        presses=[],
        bites=[],
        polls=0,
        grabs=0,
        on_grab=None,
        resets=0,
    )

    def grab():
        game.grabs += 1
        if game.on_grab:
            game.on_grab()
        return np.zeros((100, 200, 3), np.uint8)

    def movement():
        game.polls += 1
        return game.moving

    def feed(_chunk):
        result, game.bites = game.bites, []
        return result

    def observe(_frame):
        return BarObservation((80, 70, 40, 5), game.fraction, 0.95) if game.visible else None

    engine = FishingEngine(
        Config(GameConfig(cast_key="space", interact_key="space"), VisionConfig(), AudioConfig()),
        WindowRef("World of Warcraft", 1, 0, 0, 200, 100, handle=42),
        capturer=SimpleNamespace(grab=grab, close=lambda: None, open=lambda _: None),
        input_device=SimpleNamespace(
            is_game_focused=lambda _: game.focused,
            movement_keys_down=movement,
            press_key=lambda key, _: game.presses.append((key, game.now)),
        ),
        audio_source=SimpleNamespace(sample_rate=22050, read=lambda _: np.zeros(32, np.float32)),
        detector=SimpleNamespace(reset=lambda: None, feed=feed),
        bar_detector=SimpleNamespace(observe=observe, reset=lambda: None),
        clock=lambda: game.now,
        sleep=lambda _: None,
    )

    def tick(now, **changes):
        game.now = now
        for key, value in changes.items():
            setattr(game, key, value)
        engine.step()

    game.tick = tick
    game.bite = lambda: SimpleNamespace(clip="fishbite", score=0.9)
    game.configure = lambda **kw: setattr(
        engine, "cfg", replace(engine.cfg, run=replace(engine.cfg.run, **kw))
    )
    return game, engine
