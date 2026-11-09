"""Recognize the complete native fishing bar, independently of its remaining fill."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from wowfish.assets import asset_dir
from wowfish.vision.region import Region

LABEL_THRESHOLD = 0.65
REFERENCE_WIDTH = 313


@dataclass(frozen=True)
class BarObservation:
    box: tuple[int, int, int, int]  # x, y, width, height, including the label
    fraction: float
    score: float


@dataclass(frozen=True)
class _Location:
    box: tuple[int, int, int, int]
    fill: tuple[int, int, int, int]  # exclusive right/bottom
    label: tuple[int, int, int, int]


def _green(image: np.ndarray) -> np.ndarray:
    blue, green, red = np.moveaxis(image.astype(np.float32), -1, 0)
    return (green > 100) & (green > red * 1.12) & (green > blue * 1.4)


@lru_cache(maxsize=64)
def _labels(width: int) -> tuple[np.ndarray, ...]:
    result = []
    for language in ("ru", "en"):
        path = asset_dir() / "fishing_bar" / f"label-{language}.png"
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise RuntimeError(f"Missing fishing bar template: {path}")
        for factor in (0.95, 0.975, 1.0, 1.025, 1.05):
            scale = width / REFERENCE_WIDTH * factor
            result.append(cv2.resize(template, None, fx=scale, fy=scale))
    return tuple(result)


def _label_score(image: np.ndarray, location: _Location) -> float:
    x0, y0, x1, y1 = location.label
    patch = image[y0:y1, x0:x1]
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    best = -1.0
    for template in _labels(location.box[2]):
        if template.shape[0] <= patch.shape[0] and template.shape[1] <= patch.shape[1]:
            _, score, _, where = cv2.minMaxLoc(
                cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED)
            )
            center_offset = abs(where[0] + template.shape[1] / 2 - patch.shape[1] / 2)
            if center_offset <= max(6, location.box[2] * 0.02):
                best = max(best, score)
    return best


def _fraction(frame: np.ndarray, bounds: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = bounds
    patch = frame[y0:y1, x0:x1]
    columns = np.flatnonzero(_green(patch).sum(axis=0) >= max(2, patch.shape[0] * 0.35))
    if not len(columns) or columns[0] > max(5, patch.shape[1] * 0.03):
        return 0.0
    # A successful hook makes the frame glow green, including the far right border.
    # Measure the fill connected to the left; isolated border pixels are not progress.
    gaps = np.flatnonzero(np.diff(columns) > max(3, patch.shape[1] * 0.01))
    end = columns[gaps[0]] if len(gaps) else columns[-1]
    return min(1.0, float(end + 1) / patch.shape[1])


class FishingBarDetector:
    """Acquire by frame geometry + fishing text; track by the text below the fill.

    Other green bars are not fishing. Stable bounds retain a nearly empty bar.
    An explicit region is a user-selected fallback for another UI or language.
    """

    def __init__(self, region: Region | None = None) -> None:
        self.region = region
        self._location: _Location | None = None
        self._shape: tuple[int, int] | None = None

    def reset(self) -> None:
        self._location = None
        self._shape = None

    def observe(self, frame: np.ndarray) -> BarObservation | None:
        height, width = frame.shape[:2]
        if (height, width) != self._shape:
            self.reset()
            self._shape = (height, width)
        if self.region is not None:
            bounds = self.region.to_box(width, height)
            fraction = _fraction(frame, bounds)
            if fraction == 0:
                return None
            x0, y0, x1, y1 = bounds
            return BarObservation((x0, y0, x1 - x0, y1 - y0), fraction, 1.0)
        if self._location is not None:
            score = _label_score(frame, self._location)
            if score >= LABEL_THRESHOLD:
                return BarObservation(
                    self._location.box,
                    _fraction(frame, self._location.fill),
                    score,
                )
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = self._acquire(frame, gray)
        if found is None:
            return None
        self._location, score = found
        return BarObservation(self._location.box, _fraction(frame, self._location.fill), score)

    def _acquire(self, frame: np.ndarray, gray: np.ndarray) -> tuple[_Location, float] | None:
        height, width = frame.shape[:2]
        x0, y0 = int(width * 0.29), int(height * 0.52)
        area = frame[y0 : int(height * 0.88), x0 : int(width * 0.71)]
        stats = cv2.connectedComponentsWithStats(_green(area).astype(np.uint8))[2][1:]
        best: tuple[_Location, float] | None = None
        for xx, yy, gw, gh, pixels in stats:
            if not (6 <= gw <= width * 0.25 and 5 <= gh <= height * 0.035 and pixels >= 35):
                continue
            gx, gy = x0 + int(xx), y0 + int(yy)
            left, top = max(0, gx - 10), max(0, gy - 8)
            edges = cv2.Canny(
                gray[top : gy + gh + 9, left : min(width, gx + int(width * 0.25))], 40, 110
            )
            lines = cv2.HoughLinesP(
                edges, 1, np.pi / 180, 35, minLineLength=max(60, int(gw * 0.8)), maxLineGap=6
            )
            if lines is None:
                continue
            borders = [
                (int(a) + left, int(c) + left)
                for a, by, c, dy in lines[:, 0]
                if abs(int(by) - int(dy)) <= 1
                and a + left <= gx + 8
                and c + left >= gx + gw - 5
                and width * 0.07 <= c - a <= width * 0.25
            ]
            if not borders:
                continue
            lx, rx = max(borders, key=lambda line: line[1] - line[0])
            bottom = min(height, gy + gh + int(gh * 1.8))
            location = _Location(
                (lx, max(0, gy - 4), rx - lx + 1, bottom - max(0, gy - 4)),
                (lx + 2, gy, rx + 1, gy + int(gh)),
                (lx, gy + int(gh) - 1, rx + 1, bottom),
            )
            score = _label_score(gray, location)
            if score >= LABEL_THRESHOLD and (best is None or score > best[1]):
                best = location, score
        return best


def find_cast_bar(frame: np.ndarray) -> Region | None:
    """One-frame convenience API. Use FishingBarDetector for continuous tracking."""
    observed = FishingBarDetector().observe(frame)
    if observed is None:
        return None
    height, width = frame.shape[:2]
    x, y, w, h = observed.box
    return Region(x / width, y / height, (x + w) / width, (y + h) / height)
