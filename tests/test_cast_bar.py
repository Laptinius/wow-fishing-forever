from pathlib import Path

import cv2
import numpy as np
import pytest

from wowfish.config import ConfigError, load_config
from wowfish.vision.cast_bar import FishingBarDetector

FIXTURES = Path(__file__).parent / "fixtures"


def frame(name):
    crop = cv2.imread(str(FIXTURES / f"fishing-bar-{name}.png"))
    image = np.zeros((1369, 2560, 3), np.uint8)
    image[850 : 850 + crop.shape[0], 1050 : 1050 + crop.shape[1]] = crop
    return image


def test_real_full_nearly_empty_and_absent_bar():
    detector = FishingBarDetector()
    full = detector.observe(frame("full"))
    assert full is not None and 0.65 < full.fraction < 0.85
    # Independently acquire a nearly empty bar, without a previously seen full one.
    low = FishingBarDetector().observe(frame("low"))
    assert low is not None and 0.04 < low.fraction < 0.15
    assert abs(low.box[2] - full.box[2]) < 4
    assert detector.observe(frame("absent")) is None
    assert FishingBarDetector().observe(frame("absent")) is None


@pytest.mark.parametrize("scale", [0.75, 1.0, 1.25, 1.5])
def test_bar_reacquires_after_resolution_change(scale):
    image = frame("full")
    detector = FishingBarDetector()
    assert detector.observe(image) is not None
    changed = cv2.resize(image, None, fx=scale, fy=scale)
    result = detector.observe(changed)
    assert result is not None
    assert result.box[2] == pytest.approx(313 * scale, abs=8)


def test_english_fishing_label():
    assert FishingBarDetector().observe(frame("english")) is not None


def test_moved_bar_updates_geometry_instead_of_using_old_cached_rectangle():
    detector = FishingBarDetector()
    first = detector.observe(frame("full"))
    moved = np.roll(frame("full"), 40, axis=1)
    second = detector.observe(moved)
    assert first is not None and second is not None
    assert second.box[0] == pytest.approx(first.box[0] + 40, abs=3)
    assert second.fraction == pytest.approx(first.fraction, abs=0.02)


def test_green_bar_without_fishing_label_is_not_fishing():
    image = np.zeros((1369, 2560, 3), np.uint8)
    cv2.rectangle(image, (1100, 900), (1413, 917), (70, 80, 90), 2)
    image[903:916, 1102:1390] = (20, 190, 70)
    assert FishingBarDetector().observe(image) is None


def test_cached_location_does_not_treat_background_as_bar():
    detector = FishingBarDetector()
    assert detector.observe(frame("full")) is not None
    image = frame("full")
    image[900:980] = 0
    assert detector.observe(image) is None


def test_green_success_border_is_not_mistaken_for_full_progress():
    detector = FishingBarDetector()
    before = detector.observe(frame("before-success"))
    after = detector.observe(frame("success"))
    assert before is not None and after is not None
    assert 0.25 < before.fraction < 0.5
    assert after.fraction == pytest.approx(before.fraction, abs=0.04)


def test_tracking_keeps_bar_with_zero_fill_but_visible_fishing_label():
    detector = FishingBarDetector()
    image = frame("full")
    found = detector.observe(image)
    assert found is not None
    x, y, w, _h = found.box
    image[y : y + 22, x : x + w] = 0
    empty = detector.observe(image)
    assert empty is not None
    assert empty.fraction == 0


def test_cast_bar_region_rejects_invalid_rectangles(tmp_path):
    config = tmp_path / "config.toml"
    for value in ("0,0,1", "0,0,2,1", "1,0,0,1", "wrong"):
        config.write_text(f'[vision]\ncast_bar_region = "{value}"\n')
        with pytest.raises(ConfigError, match="cast_bar_region"):
            load_config(config)
