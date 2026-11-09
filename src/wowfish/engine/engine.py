"""Observe the fishing bar continuously; all actions require fresh, focused game state."""

from __future__ import annotations

import math
import queue
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2

from wowfish.audio import AudioChunk
from wowfish.audio._threaded import ThreadedSource
from wowfish.capture import WindowRef, find_window, get_capturer, linux_x11
from wowfish.config import Config
from wowfish.engine import events as ev
from wowfish.engine.state import State
from wowfish.vision.cast_bar import BarObservation, FishingBarDetector

UNFOCUSED = "World of Warcraft is not the focused window; click into the game"
MOVEMENT_PAUSED = "WASD: movement pause; fishing will resume automatically"
FRAME_INTERVAL_S = 0.1
ABSENCE_CONFIRM_S = 0.15
PREVIEW_INTERVAL_S = 0.25


class FishingEngine:
    def __init__(
        self,
        cfg: Config,
        window: WindowRef,
        *,
        capturer: Any,
        input_device: Any,
        audio_source: Any,
        detector: Any,
        bar_detector: Any = None,
        find_window: Callable[..., WindowRef | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        session_s: float | None = None,
    ) -> None:
        self.cfg, self.window = cfg, window
        self.capturer, self.input = capturer, input_device
        self.audio, self.detector = audio_source, detector
        self.bar_detector = bar_detector or FishingBarDetector(cfg.vision.bar_region())
        self._find_window, self._clock, self._sleep = find_window, clock, sleep
        self.session_s = cfg.game.session_minutes * 60 if session_s is None else session_s
        self.events: queue.Queue[ev.Event] = queue.Queue()
        self.state = State.IDLE
        self.stats = ev.Stats(0.0)
        self.started: float | None = None
        self._stop = False
        self._available = True
        self._last_window = self._last_frame = self._last_preview = -math.inf
        self._last_progress = self._last_stats = self._last_listen = -math.inf
        self._last_scores_sent = -math.inf
        self._phase: tuple[State, str, str] | None = None
        self._progress_kind = ""
        self._pause_reason = ""
        self._observation: BarObservation | None = None
        self._cycle_active = False
        self._cycle_serial = 0
        self._cycle_started = self._arm_until = 0.0
        self._last_fraction: float | None = None
        self._absent_since: float | None = None
        self._pending_cast = False
        self._failed_casts = 0
        self._hook_at: float | None = None
        self._ready_at: float | None = None
        self._movement_down = False
        self._movement_until = 0.0
        self._audio_active = False
        self._last_audio: float | None = None
        self._audio_frames = 0
        self._best_heard = ("", -1.0)

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            self.capturer.open(self.window)
            with self.audio:
                while self.state is not State.STOPPED:
                    self.step()
        finally:
            self.capturer.close()

    def step(self) -> None:
        if self.state is State.STOPPED:
            return
        now = self._clock()
        if self.started is None:
            self.started = now
            self._last_audio = now
        if self._stop:
            self._finish("user")
            return
        if self.session_s and now - self.started >= self.session_s:
            self._finish("session limit")
            return
        try:
            self._step(now)
            self._maybe_stats(now)
            self._sleep(0.02)
        except Exception as error:
            self._emit(ev.Error(self._t(), f"{type(error).__name__}: {error}"))
            self._finish("error")

    def _step(self, now: float) -> None:
        self._refresh_window(now)
        chunks = self._drain_audio(now)
        if not self._available or self.input.is_game_focused(self.window) is not True:
            # Time in another application is not evidence that fishing ended.
            self._observation = None
            self._absent_since = None
            self._ready_at = self._hook_at = None
            self._last_frame = -math.inf
            self._audio_active = False
            self._pause(UNFOCUSED)
            self._progress("unfocused", "Пауза — вернись в окно WoW", 0, now=now)
            return

        movement = self._movement_paused(now)
        self._observe(now)
        if movement:
            self._audio_active = False
            self._hook_at = None
            self._pause(MOVEMENT_PAUSED)
            remaining = max(0.0, self._movement_until - now)
            text = (
                "Пауза — удерживается WASD"
                if self._movement_down
                else f"После WASD: {remaining:.1f} сек."
            )
            self._progress(
                "movement",
                text,
                remaining / max(self.cfg.run.movement_pause_s, 0.01),
                remaining,
                now=now,
            )
            return

        if self._observation is not None:
            self._watch_cast(now, chunks)
        else:
            self._wait_for_cast(now)

    def _watch_cast(self, now: float, chunks: list[AudioChunk]) -> None:
        """Wait for the splash or confirm the result of the last hook."""
        assert self._observation is not None
        self._ready_at = None
        if self._hook_at is not None:
            if now - self._hook_at >= self.cfg.run.loot_timeout_s:
                self._emit(ev.Timeout(self._t(), "loot", self.cfg.run.loot_timeout_s))
                self._hook_at = None
                self._audio_active = False
                self._change(
                    State.WAIT_BITE, "Полоска осталась после подсечки", "Продолжаю слушать"
                )
            else:
                remaining = self.cfg.run.loot_timeout_s - (now - self._hook_at)
                self._change(
                    State.INTERACT,
                    "Пробел отправлен для подсечки",
                    "Проверяю исчезновение полоски",
                )
                self._progress(
                    "hook",
                    f"Подсечка — проверка: {remaining:.1f} сек.",
                    remaining / self.cfg.run.loot_timeout_s,
                    remaining,
                    now=now,
                )
                return
        self._change(State.WAIT_BITE, "Полоска рыбалки видна", "Слушаю всплеск только из WoW")
        if now < self._arm_until:
            self._audio_active = False
            remaining = self._arm_until - now
            self._progress(
                "arming",
                f"Начало заброса: {remaining:.1f} сек.",
                remaining / max(self.cfg.run.bite_arm_s, 0.01),
                remaining,
                now=now,
            )
            return
        self._progress(
            "fishing",
            f"Рыбалка — слушаю всплеск · {self._observation.fraction:.0%}",
            self._observation.fraction,
            now=now,
        )
        if not self._audio_active:
            self.detector.reset()
            self._audio_active = True
            return  # Discard audio captured before this cast was armed.
        bites = [bite for chunk in chunks for bite in self.detector.feed(chunk)]
        self._note_scores(now, bool(bites))
        if bites:
            self._try_hook(max(bites, key=lambda item: item.score))

    def _try_hook(self, bite: Any) -> None:
        """Recheck the current cast so a delayed sound cannot act on a new one."""
        serial = self._cycle_serial
        if not self._action_frame() or self._observation is None or serial != self._cycle_serial:
            return  # The player cancelled/recast while this sound was being processed.
        self.stats = replace(self.stats, bites_heard=self.stats.bites_heard + 1)
        self._emit(ev.BiteHeard(self._t(), bite.clip, float(bite.score)))
        if self._press(self.cfg.game.interact_key, "bite"):
            self._hook_at = self._clock()
            self._audio_active = False
            self._change(
                State.INTERACT,
                "Услышал всплеск — нажал пробел",
                "Проверяю завершение рыбалки",
            )

    def _wait_for_cast(self, now: float) -> None:
        """One independent absence deadline, shared by failed and manual casts."""
        self._audio_active = False
        if self._absent_since is None:
            return
        deadline = self._ready_at
        total = self.cfg.run.post_catch_delay_s
        if deadline is None:
            deadline = self._absent_since + self.cfg.run.recast_delay_s
            total = self.cfg.run.recast_delay_s
        remaining = max(0.0, deadline - now)
        phase = State.CASTING if self._pending_cast else State.CHECKING
        text = "Проверяю новый заброс" if self._pending_cast else "Полоски нет — готовлю заброс"
        self._change(phase, text, "Заброшу, если полоска не появится")
        self._progress(
            "recast", f"{text}: {remaining:.1f} сек.", remaining / total, remaining, now=now
        )
        if now >= deadline and not self._cycle_active:
            self._cast()

    def _observe(self, now: float, *, force: bool = False) -> None:
        if not force and now - self._last_frame < FRAME_INTERVAL_S:
            return
        frame = self.capturer.grab()
        observation = self.bar_detector.observe(frame)
        self._last_frame = now
        self._observation = observation
        if observation is not None:
            refill = (
                self._hook_at is None
                and self._last_fraction is not None
                and observation.fraction - self._last_fraction > 0.3
            )
            if not self._cycle_active or refill:
                fresh = self._pending_cast or self._absent_since is not None or refill
                self._begin_cycle(now, observation, fresh=fresh)
            self._last_fraction = observation.fraction
            self._absent_since = None
        else:
            if self._absent_since is None:
                self._absent_since = now
            if self._cycle_active and now - self._absent_since >= ABSENCE_CONFIRM_S:
                self._end_cycle(now)
        if now - self._last_preview >= PREVIEW_INTERVAL_S:
            self._last_preview = now
            scale = min(1.0, 480 / frame.shape[1])
            thumb = cv2.resize(
                frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale))
            )
            box = tuple(round(v * scale) for v in observation.box) if observation else None
            self._emit(ev.FrameObserved(self._t(), thumb, box))

    def _end_cycle(self, now: float) -> None:
        """Retire one confirmed channel exactly once."""
        self._cycle_active = False
        self._last_fraction = None
        if self._hook_at is not None:
            self.stats = replace(self.stats, catches=self.stats.catches + 1)
            self._emit(ev.Caught(self._t()))
            self._ready_at = now + self.cfg.run.post_catch_delay_s
        else:
            self.stats = replace(self.stats, misses=self.stats.misses + 1)
            self._emit(
                ev.Timeout(
                    self._t(),
                    "bar",
                    now - self._cycle_started,
                    "Полоска исчезла без автоматической подсечки",
                )
            )
        self._hook_at = None
        self._audio_active = False

    def _begin_cycle(self, now: float, observation: BarObservation, *, fresh: bool) -> None:
        manual = not self._pending_cast
        if manual:
            self.stats = replace(self.stats, casts=self.stats.casts + 1)
        self._pending_cast = False
        self._failed_casts = 0
        self._cycle_active = True
        self._cycle_serial += 1
        self._cycle_started = now
        # An already running cast has unknown age: listen immediately. Only a known new
        # cast needs suppression of the initial water-entry sound.
        self._arm_until = now + (
            self.cfg.run.bite_arm_s if fresh and observation.fraction > 0.85 else 0
        )
        self._ready_at = self._hook_at = None
        self._audio_active = False
        self._best_heard = ("", -1.0)
        self.detector.reset()
        self._emit(ev.CastSeen(self._t(), manual))

    def _cast(self) -> None:
        # Manual casting can happen while a countdown is finishing. Check a new frame first.
        if not self._action_frame() or self._observation is not None:
            return
        key = self.cfg.game.cast_key
        failed = self._pending_cast
        if (failed or self._failed_casts) and self.cfg.game.recast_key:
            key = self.cfg.game.recast_key
        if not self._press(key, "cast"):
            return
        if failed:
            self._failed_casts += 1
            self.stats = replace(self.stats, misses=self.stats.misses + 1)
            self._emit(
                ev.Timeout(
                    self._t(),
                    "cast",
                    self.cfg.run.recast_delay_s,
                    "После нажатия полоска не появилась",
                )
            )
        self.stats = replace(self.stats, casts=self.stats.casts + 1)
        self._pending_cast = True
        self._absent_since = self._clock()
        self._ready_at = None
        self._change(State.CASTING, f"Новый заброс — {key}", "Жду появления полоски")

    def _movement_paused(self, now: float) -> bool:
        if not self.cfg.run.movement_pause_enabled:
            self._movement_down = False
            self._movement_until = 0.0
            return False
        down = bool(self.input.movement_keys_down())
        if down or self._movement_down:
            self._movement_until = now + self.cfg.run.movement_pause_s
        self._movement_down = down
        return down or now < self._movement_until

    def _press(self, key: str, why: str) -> bool:
        if self._stop or self.input.is_game_focused(self.window) is not True:
            return False
        if self._movement_paused(self._clock()):
            return False
        self.input.press_key(key, self.cfg.run.key_hold_ms / 1000)
        self._emit(ev.KeyPressed(self._t(), key, why))
        return True

    def _action_frame(self) -> bool:
        """Refresh window geometry too: the player may move it during the countdown."""
        now = self._clock()
        self._refresh_window(now, force=True)
        if self._stop or not self._available or self.input.is_game_focused(self.window) is not True:
            return False
        self._observe(now, force=True)
        return True

    def _refresh_window(self, now: float, *, force: bool = False) -> None:
        if self._find_window is None or (not force and now - self._last_window < 0.5):
            return
        self._last_window = now
        found = self._find_window("World of Warcraft")
        self._available = found is not None
        if found is not None and found != self.window:
            if found.pid != self.window.pid:
                raise RuntimeError("WoW перезапущен: запусти рыбалку заново для подключения звука")
            self.capturer.close()
            self.capturer.open(found)
            self.window = found
            self.bar_detector.reset()
            self._last_frame = -math.inf

    def _drain_audio(self, now: float) -> list[AudioChunk]:
        rate = self.audio.sample_rate
        last = self._last_audio if self._last_audio is not None else now
        count = max(1024, min(int((now - last) * rate), int(2 * rate)))
        self._last_audio = now
        samples = self.audio.read(count)
        self._audio_frames += len(samples)
        return [AudioChunk(samples, rate, self._audio_frames / rate)] if len(samples) else []

    def _note_scores(self, now: float, flush: bool) -> None:
        best = getattr(self.detector, "best_heard", None)
        if best is not None and best.score > self._best_heard[1]:
            self._best_heard = best.name, float(best.score)
        take = getattr(self.detector, "take_peaks", None)
        if take and (flush or now - self._last_scores_sent >= 0.1):
            self._last_scores_sent = now
            self._emit(
                ev.Scores(
                    self._t(),
                    take(),
                    self.cfg.audio.bite_threshold,
                    float(getattr(self.detector, "level_dbfs", -120.0)),
                )
            )

    def _pause(self, reason: str) -> None:
        self.state = State.PAUSED
        if self._pause_reason != reason:
            self._pause_reason = reason
            self._phase = None
            self._emit(ev.Paused(self._t(), reason))

    def _change(self, state: State, did: str, next_action: str) -> None:
        phase = state, did, next_action
        if self._phase == phase:
            return
        if self._pause_reason:
            self._pause_reason = ""
            self._emit(ev.Resumed(self._t(), state))
        self._phase = phase
        self.state = state
        self._emit(ev.StateChanged(self._t(), state, did, next_action))
        self._emit_stats()

    def _progress(
        self, kind: str, text: str, fraction: float, remaining: float | None = None, *, now: float
    ) -> None:
        if kind != self._progress_kind or now - self._last_progress >= 0.1:
            self._last_progress, self._progress_kind = now, kind
            self._emit(ev.Progress(self._t(), kind, text, min(1.0, max(0.0, fraction)), remaining))

    def _maybe_stats(self, now: float) -> None:
        if self.state is State.WAIT_BITE and now - self._last_listen >= 2:
            self._last_listen = now
            self._emit(ev.Listening(self._t(), *self._best_heard, self.cfg.audio.bite_threshold))
        if now - self._last_stats >= 5:
            self._emit_stats()

    def _emit_stats(self) -> None:
        self._last_stats = self._clock()
        self.stats = replace(self.stats, t=self._t(), elapsed_s=self._t())
        self._emit(self.stats)

    def _finish(self, reason: str) -> None:
        self.state = State.STOPPED
        self._emit_stats()
        kind = "error" if reason == "error" else "stopped"
        self._emit(
            ev.Progress(
                self._t(), kind, "Ошибка — см. журнал" if kind == "error" else "Остановлено"
            )
        )
        self._emit(ev.Stopped(self._t(), reason))

    def _emit(self, event: ev.Event) -> None:
        self.events.put(event)

    def _t(self) -> float:
        return 0.0 if self.started is None else self._clock() - self.started


def unsupported_platform() -> str:
    """Why the bot cannot run here, or "" when it can (X11/Xwayland on Linux, or Windows)."""
    import os
    import sys

    if sys.platform == "win32" or linux_x11():
        return ""
    if sys.platform == "linux" and not os.environ.get("DISPLAY"):
        return "DISPLAY is not set; the bot needs X11 or Xwayland (log into a session with one)"
    return "the bot is not supported yet on this platform (Linux X11/Xwayland and Windows only)"


def build_engine(
    cfg: Config,
    *,
    session_s: float | None = None,
    title: str = "World of Warcraft",
    record_audio: Path | None = None,
) -> FishingEngine:
    from wowfish.audio._recording import RecordingSource
    from wowfish.audio.detector import detector_for
    from wowfish.audio.devices import open_capture
    from wowfish.input import get_input_device

    problem = unsupported_platform()
    if problem:
        raise RuntimeError(problem)
    window = find_window(title)
    if window is None:
        raise RuntimeError(f"no window titled {title!r}; is the game running?")
    audio: Any = ThreadedSource(open_capture(cfg.audio.selector, pid=window.pid, title=title))
    if record_audio is not None:
        audio = RecordingSource(audio, record_audio)
    return FishingEngine(
        cfg,
        window,
        capturer=get_capturer(),
        input_device=get_input_device(),
        audio_source=audio,
        detector=detector_for(cfg.audio),
        find_window=lambda _title: find_window(title),
        session_s=session_s,
    )
