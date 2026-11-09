from pathlib import Path

import cv2
import pytest

from wowfish.vision.matcher import IconMatcher
from wowfish.vision.template import load_icon_templates, template_for_size

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("size", [18, 19, 24, 27])
def test_terrain_from_failed_session_is_not_a_bobber(size):
    # Crop of the empty-water screenshot supplied on 2026-11-08.
    frame = cv2.imread(str(FIXTURES / "terrain-no-bobber.png"))
    matcher = IconMatcher(template_for_size(load_icon_templates(), size))
    raw = matcher.match(frame, full_width=2532, validate_shape=False)
    checked = matcher.match(frame, full_width=2532)
    assert raw.score >= 0.68  # The previous detector accepted this patch of ground.
    assert not checked.accepted(0.68)


@pytest.mark.parametrize(
    ("fixture", "size", "width"),
    [("gear-close.png", 30, 1158), ("gear-wide.png", 27, 2552)],
)
def test_shape_filter_keeps_real_gears_from_game_screenshots(fixture, size, width):
    frame = cv2.imread(str(FIXTURES / fixture))
    matcher = IconMatcher(template_for_size(load_icon_templates(), size))
    hit = matcher.match(frame, full_width=width)
    assert hit.accepted(0.68), hit
    assert abs(hit.x - 70) <= 3 and abs(hit.y - 70) <= 3
