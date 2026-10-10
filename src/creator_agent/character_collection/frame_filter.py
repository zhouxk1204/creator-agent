"""Quality filters for candidate frames: black / solid / blur / duplicate.

Thresholds are deliberately LENIENT: anime has motion blur, dark scenes,
fades and white flashes, so over-filtering would lose valid character shots.
Every rejection records a machine-readable status plus a human-readable
reason with the measured value, so thresholds can be tuned from the manifest
statistics later.

Black/solid constants mirror scripts/split_episode.py so both tools agree on
what "black" and "solid" mean.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

SIG_SIZE = (96, 54)  # tiny grayscale signature, same as split_episode.py

BLACK_MEAN = 12.0  # below -> black screen
WHITE_MEAN = 243.0  # above -> washed-out / white flash
SOLID_STD = 12.0  # below -> near-solid color frame
BLUR_VAR_MIN = 40.0  # Laplacian variance below this = visibly blurry
DUP_SIM = 0.995  # signature similarity above this = duplicate of a kept frame

STATUS_PASSED = "passed"
STATUS_DECODE_FAILED = "decode_failed"
FILTER_STATUSES = (
    "filtered_black",
    "filtered_white",
    "filtered_solid",
    "filtered_blur",
    "filtered_duplicate",
)


@dataclass(frozen=True)
class FilterResult:
    status: str  # STATUS_PASSED or one of FILTER_STATUSES
    reason: str  # "" when passed; otherwise includes the measured value
    signature: np.ndarray  # tiny grayscale signature (for later dup checks)


def frame_signature(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, SIG_SIZE, interpolation=cv2.INTER_AREA)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """1.0 = identical; mirrors split_episode.similarity."""
    return 1.0 - float(np.mean(cv2.absdiff(a, b))) / 255.0


def blur_variance(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def check_frame(frame: np.ndarray, kept_signatures: list[np.ndarray]) -> FilterResult:
    """Run all filters in order; first failure wins.

    ``kept_signatures`` are the signatures of frames already kept FROM THE
    SAME SCENE (duplicates across scene boundaries are not expected to be
    meaningful and would over-filter recurring shots).
    """
    sig = frame_signature(frame)
    mean = float(sig.mean())
    std = float(sig.std())
    if mean < BLACK_MEAN:
        return FilterResult("filtered_black", f"mean={mean:.1f} < {BLACK_MEAN}", sig)
    if mean > WHITE_MEAN:
        return FilterResult("filtered_white", f"mean={mean:.1f} > {WHITE_MEAN}", sig)
    if std < SOLID_STD:
        return FilterResult("filtered_solid", f"std={std:.1f} < {SOLID_STD}", sig)
    var = blur_variance(frame)
    if var < BLUR_VAR_MIN:
        return FilterResult("filtered_blur", f"laplacian_var={var:.1f} < {BLUR_VAR_MIN}", sig)
    for kept in kept_signatures:
        sim = similarity(sig, kept)
        if sim >= DUP_SIM:
            return FilterResult("filtered_duplicate", f"similarity={sim:.4f} >= {DUP_SIM}", sig)
    return FilterResult(STATUS_PASSED, "", sig)
