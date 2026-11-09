"""User configuration: one TOML file, defaults merged in, unknown keys warned about."""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import tomli_w

from wowfish import paths
from wowfish.assets import default_config_path
from wowfish.vision.region import Region

_LINE_RE = re.compile(r"\(at line (\d+), column \d+\)")


class ConfigError(Exception):
    def __init__(self, path: Path, message: str, line: int | None = None) -> None:
        self.path = path
        self.message = message
        self.line = line
        super().__init__(str(self))

    def __str__(self) -> str:
        where = f"{self.path} line {self.line}" if self.line is not None else str(self.path)
        return f"Config error in {where}: {self.message}"


@dataclass(frozen=True)
class GameConfig:
    cast_key: str = "1"
    interact_key: str = "f"
    session_minutes: int = 0
    recast_key: str = "ctrl+space"


@dataclass(frozen=True)
class VisionConfig:
    icon_size_px: int = 0
    icon_native_size: int = 0
    icon_threshold: float = 0.75
    match_width: int = 1920
    cast_bar_region: str = "auto"

    def bar_region(self) -> Region | None:
        if self.cast_bar_region in ("", "auto"):
            return None
        return Region(*(float(value) for value in self.cast_bar_region.split(",")))


@dataclass(frozen=True)
class AudioConfig:
    capture: str = "game"  # "game": the game's own audio stream; "device": the loopback `device`
    device: str = ""
    bite_threshold: float = 0.64
    refractory_s: float = 3.0
    gain_db: float = 0.0

    @property
    def selector(self) -> str:
        """What to open: "game", or the loopback device selector ("" = default output monitor)."""
        return "game" if self.capture == "game" else self.device


@dataclass(frozen=True)
class RunConfig:
    recast_delay_s: float = 2.0
    post_catch_delay_s: float = 0.3
    bite_arm_s: float = 0.75
    loot_timeout_s: float = 3.0
    key_hold_ms: int = 60
    movement_pause_enabled: bool = True
    movement_pause_s: float = 2.0


@dataclass(frozen=True)
class Config:
    game: GameConfig
    vision: VisionConfig
    audio: AudioConfig
    run: RunConfig = field(default_factory=RunConfig)


_SECTIONS: dict[str, type] = {
    "game": GameConfig,
    "vision": VisionConfig,
    "audio": AudioConfig,
    "run": RunConfig,
}


def ensure_config_file(path: Path | None = None) -> bool:
    path = path or paths.config_file()
    if path.exists():
        return False
    path.write_text(default_config_path().read_text(encoding="utf-8"), encoding="utf-8")
    return True


def load_config(path: Path | None = None) -> tuple[Config, list[str]]:
    path = path or paths.config_file()
    if not path.exists():
        return Config(game=GameConfig(), vision=VisionConfig(), audio=AudioConfig()), []
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        m = _LINE_RE.search(str(e))
        raise ConfigError(path, str(e), int(m.group(1)) if m else None) from e

    warnings: list[str] = []
    built: dict[str, Any] = {}
    for section, cls in _SECTIONS.items():
        table = raw.pop(section, {})
        if not isinstance(table, dict):
            raise ConfigError(path, f"[{section}] must be a table")
        built[section] = _build_section(path, section, cls, table, warnings)
    for leftover in raw:
        warnings.append(f"unknown section [{leftover}] ignored")
    cfg = Config(**built)
    _validate(path, cfg)
    return cfg, warnings


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or paths.config_file()
    path.write_text(to_toml(cfg))


def _build_section(
    path: Path, section: str, cls: type, table: dict[str, Any], warnings: list[str]
) -> Any:
    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in table.items():
        if section == "run" and key in {
            "icon_timeout_s",
            "cast_timeout_s",
            "pause_min_s",
            "pause_max_s",
            "movement_pause_min_s",
            "movement_pause_max_s",
        }:
            continue  # Old polling/random-delay settings do not govern the bar-driven loop.
        if key not in known:
            warnings.append(f"unknown key {section}.{key} ignored")
            continue
        expected = _expected_type(known[key].type)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(
                path, f"{section}.{key} must be {expected.__name__}, got {type(value).__name__}"
            )
        kwargs[key] = value
    return cls(**kwargs)


def _expected_type(annotation: Any) -> type:
    # Annotations are strings because of `from __future__ import annotations`.
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
    base = text.replace(" | None", "").strip()
    return {"str": str, "int": int, "bool": bool, "float": float}[base]


def _validate(path: Path, cfg: Config) -> None:
    def check(ok: bool, key: str, rule: str) -> None:
        if not ok:
            raise ConfigError(path, f"{key} {rule}")

    check(cfg.game.session_minutes >= 0, "game.session_minutes", "must be 0 (forever) or positive")
    v, a = cfg.vision, cfg.audio
    check(0.0 < v.icon_threshold <= 1.0, "vision.icon_threshold", "must be in (0, 1]")
    check(v.match_width >= 320, "vision.match_width", "must be at least 320")
    check(v.icon_size_px >= 0, "vision.icon_size_px", "must be 0 or positive")
    check(v.icon_native_size >= 0, "vision.icon_native_size", "must be 0 or positive")
    try:
        v.bar_region()
    except (TypeError, ValueError) as e:
        raise ConfigError(
            path, "vision.cast_bar_region must be auto or left,top,right,bottom in 0..1"
        ) from e
    check(a.capture in ("game", "device"), "audio.capture", 'must be "game" or "device"')
    check(0.0 < a.bite_threshold <= 1.0, "audio.bite_threshold", "must be in (0, 1]")
    check(a.refractory_s >= 0.0, "audio.refractory_s", "must be 0 or positive")
    check(0.0 <= a.gain_db <= 60.0, "audio.gain_db", "must be between 0 and 60")
    r = cfg.run
    check(0.5 <= r.recast_delay_s <= 30, "run.recast_delay_s", "must be between 0.5 and 30")
    check(0.1 <= r.post_catch_delay_s <= 5, "run.post_catch_delay_s", "must be between 0.1 and 5")
    check(0 <= r.bite_arm_s <= 3, "run.bite_arm_s", "must be between 0 and 3")
    check(0.5 <= r.loot_timeout_s <= 10, "run.loot_timeout_s", "must be between 0.5 and 10")
    check(10 <= r.key_hold_ms <= 500, "run.key_hold_ms", "must be between 10 and 500")
    check(
        0 <= r.movement_pause_s <= 60,
        "run.movement_pause_s",
        "must be between 0 and 60",
    )


def to_toml(cfg: Config) -> str:
    return tomli_w.dumps(asdict(cfg))
