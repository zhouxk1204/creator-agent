"""Quality filters: black / white / solid / blur / duplicate, with reasons."""

import cv2
import numpy as np
import pytest

from creator_agent.character_collection.frame_filter import (
    BLUR_VAR_MIN,
    DUP_SIM,
    STATUS_PASSED,
    check_frame,
    frame_signature,
    similarity,
)

rng = np.random.default_rng(42)


def sharp_frame() -> np.ndarray:
    """Blocky random texture: structured like real imagery (survives the
    INTER_AREA downscale in frame_signature), high Laplacian variance."""
    small = rng.integers(40, 220, size=(45, 80, 3), dtype=np.uint8)
    return cv2.resize(small, (480, 270), interpolation=cv2.INTER_NEAREST)


class TestCheckFrame:
    def test_sharp_textured_frame_passes(self):
        result = check_frame(sharp_frame(), [])
        assert result.status == STATUS_PASSED
        assert result.reason == ""

    def test_black_frame_filtered(self):
        result = check_frame(np.zeros((270, 480, 3), np.uint8), [])
        assert result.status == "filtered_black"
        assert "mean=" in result.reason

    def test_white_flash_filtered(self):
        result = check_frame(np.full((270, 480, 3), 255, np.uint8), [])
        assert result.status == "filtered_white"

    def test_solid_color_filtered(self):
        result = check_frame(np.full((270, 480, 3), 128, np.uint8), [])
        assert result.status == "filtered_solid"
        assert "std=" in result.reason

    def test_blurry_frame_filtered(self):
        # 15x15: blurry (Laplacian var < BLUR_VAR_MIN) but not yet "solid".
        blurred = cv2.GaussianBlur(sharp_frame(), (15, 15), 0)
        var = cv2.Laplacian(cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        assert var < BLUR_VAR_MIN, "test fixture must actually be blurry"
        result = check_frame(blurred, [])
        assert result.status == "filtered_blur"
        assert "laplacian_var=" in result.reason

    def test_duplicate_against_kept_frame(self):
        frame = sharp_frame()
        kept = [frame_signature(frame)]
        result = check_frame(frame.copy(), kept)
        assert result.status == "filtered_duplicate"
        assert "similarity=" in result.reason

    def test_different_frame_not_duplicate(self):
        a, b = sharp_frame(), sharp_frame()
        assert similarity(frame_signature(a), frame_signature(b)) < DUP_SIM
        result = check_frame(b, [frame_signature(a)])
        assert result.status == STATUS_PASSED

    def test_filter_order_black_before_solid(self):
        # A black frame is also "solid"; black must win for clearer stats.
        result = check_frame(np.zeros((270, 480, 3), np.uint8), [])
        assert result.status == "filtered_black"

    @pytest.mark.parametrize("value", [5, 50, 120])
    def test_dark_solid_is_black_or_solid_not_pass(self, value):
        result = check_frame(np.full((270, 480, 3), value, np.uint8), [])
        assert result.status in {"filtered_black", "filtered_solid"}
