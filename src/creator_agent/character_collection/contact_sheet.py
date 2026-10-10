"""Paged contact sheets for human review of candidate frames.

Each page is a labelled grid (max COLS x ROWS cells) so an episode never
produces one gigantic image. Labels are pure ASCII (candidate short ID +
quality status) because OpenCV's Hershey fonts cannot draw Japanese text;
the full metadata lives in the manifest.

Original candidate images stay in inbox/ — the thumbnails here are for
browsing only and are not reference material.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import cv2
import numpy as np

from .frame_filter import STATUS_PASSED
from .manifest import CandidateRecord
from .naming import parse_candidate_id

THUMB_W, THUMB_H = 320, 180
LABEL_H = 40
COLS = 4
ROWS = 5
PAGE_SIZE = COLS * ROWS  # 20 candidates per page

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _short_label(rec: CandidateRecord) -> tuple[str, str]:
    """Two ASCII label lines: compact ID + quality status."""
    cid = parse_candidate_id(rec.candidate_id)
    if cid is not None:
        id_part = f"{cid.scene_id}_T{cid.timestamp_ms:06d}_C{cid.ordinal:02d}"
    else:
        id_part = rec.candidate_id[-28:]
    status = "OK" if rec.quality_status == STATUS_PASSED else rec.quality_status.replace("filtered_", "F:")
    return id_part, status


def _labelled_cell(image_path: Path, rec: CandidateRecord) -> np.ndarray:
    cell = np.full((THUMB_H + LABEL_H, THUMB_W, 3), 32, np.uint8)
    img = cv2.imread(str(image_path)) if image_path.is_file() else None
    if img is not None:
        cell[LABEL_H : LABEL_H + THUMB_H] = cv2.resize(img, (THUMB_W, THUMB_H))
    else:
        cv2.putText(cell, "image missing", (70, LABEL_H + THUMB_H // 2), _FONT, 0.7, (90, 90, 90), 1)
    line1, line2 = _short_label(rec)
    cv2.putText(cell, line1, (6, 17), _FONT, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    color = (120, 220, 120) if line2 == "OK" else (80, 80, 220)
    cv2.putText(cell, line2, (6, 35), _FONT, 0.45, color, 1, cv2.LINE_AA)
    return cell


def build_contact_sheets(
    records: list[CandidateRecord],
    library_root: Path,
    episode_id: str,
    out_dir: Path | None = None,
) -> list[Path]:
    """Write paged contact sheets for ``records`` (any quality status).

    Returns the list of written page paths. Never raises on individual image
    problems (a missing file renders as a placeholder cell).
    """
    out_dir = out_dir or (library_root / "previews" / episode_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = sorted(records, key=lambda r: r.candidate_id)
    if not records:
        return []

    pages = math.ceil(len(records) / PAGE_SIZE)
    written: list[Path] = []
    digits = max(3, len(str(pages)))
    for page in range(pages):
        chunk = records[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        sheet = np.full((ROWS * (THUMB_H + LABEL_H), COLS * THUMB_W, 3), 24, np.uint8)
        for i, rec in enumerate(chunk):
            r, c = divmod(i, COLS)
            image_path = library_root / rec.image_path if rec.image_path else Path("<none>")
            cell = _labelled_cell(image_path, rec)
            sheet[
                r * (THUMB_H + LABEL_H) : (r + 1) * (THUMB_H + LABEL_H),
                c * THUMB_W : (c + 1) * THUMB_W,
            ] = cell
        out_path = out_dir / f"contact_sheet_{page + 1:0{digits}d}.jpg"
        if cv2.imwrite(str(out_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            written.append(out_path)
        else:
            print(f"警告: 联系表写入失败 {out_path}", file=sys.stderr)
    # Remove stale pages from a previous, longer run so page count stays truthful.
    for stale in sorted(out_dir.glob("contact_sheet_*.jpg")):
        if stale not in written:
            stale.unlink()
    return written
