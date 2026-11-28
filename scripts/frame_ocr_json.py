"""Write ``ocr/frames.json`` from an existing task directory.

Every frame that carries recognised text gets one record (frame, time, text,
confidence, group, in_srt); frames without text are omitted. Reuses the
previous run's ``groups/groups.json`` and ``frame_index.csv`` so no video
decode or OCR is needed.

Usage: python scripts/frame_ocr_json.py <task dir>
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def load_pts(task: Path) -> dict[int, float]:
    """Map frame index to presentation time."""
    return load_pts_file(task / "frame_index.csv")


def load_pts_file(path: Path) -> dict[int, float]:
    """Map frame index to presentation time from a frame_index.csv."""
    pts: dict[int, float] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pts[int(row["frame_index"])] = float(row["pts_time"])
    return pts


def main(task_dir: str, index_path: str | None = None) -> int:
    task = Path(task_dir)
    groups_file = task / "groups" / "groups.json"
    if not groups_file.is_file():
        print(f"missing {groups_file}")
        return 1
    groups = json.loads(groups_file.read_text(encoding="utf-8"))
    index = Path(index_path) if index_path else task / "frame_index.csv"
    if not index.is_file():
        print(f"missing {index}; pass the frame_index.csv of the source run as the second argument")
        return 1
    pts = load_pts(index.parent) if index.name == "frame_index.csv" else load_pts_file(index)

    records: list[dict[str, object]] = []
    for group in groups:
        text = group.get("text")
        if not text or group.get("status") not in {"ok", "low_confidence"}:
            continue
        last_frame = int(group["end_frame"]) - int(group.get("missing_frames") or 0)
        for frame in range(int(group["start_frame"]), last_frame + 1):
            records.append(
                {
                    "frame": frame,
                    "time": round(pts.get(frame, 0.0), 3),
                    "text": text,
                    "confidence": round(float(group.get("confidence") or 0.0), 4),
                    "group_id": group["group_id"],
                    "in_srt": bool(group.get("in_srt")),
                }
            )

    target = task / "ocr" / "frames.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(records)} frames with text -> {target}")
    return 0


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "output/demo_srt_ocr2"
    index = sys.argv[2] if len(sys.argv) > 2 else None
    raise SystemExit(main(task, index))
