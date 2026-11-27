"""Rebuild result.srt from an existing groups/groups.json.

Useful when only the SRT shipping rules changed: no OCR or detection re-run is
needed, the stored texts and timings are reused.
"""

import json
import sys
from pathlib import Path

from creator_agent.subtitle_extractor.extraction import ExtractionConfig, is_srt_worthy
from creator_agent.subtitle_extractor.srt import SrtCue, build_srt


def rebuild(task_dir: Path, config: ExtractionConfig | None = None) -> int:
    config = config or ExtractionConfig()
    groups = json.loads((task_dir / "groups" / "groups.json").read_text(encoding="utf-8"))
    for group in groups:
        text = group.get("text") or ""
        duration = float(group.get("duration") or 0.0)
        confidence = float(group.get("confidence") or 0.0)
        group["in_srt"] = is_srt_worthy(text, group.get("status", ""), confidence, duration, config)

    cues = [
        SrtCue(float(group["start_pts"]), float(group["end_pts"]), group["text"], group["group_id"])
        for group in groups
        if group["in_srt"]
    ]
    srt_text = build_srt(cues)
    (task_dir / "result.srt").write_text(srt_text, encoding="utf-8")
    (task_dir / "groups" / "groups.json").write_text(
        json.dumps(groups, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    result_path = task_dir / "result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["groups"] = groups
        result["summary"]["srt_cues"] = len(cues)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(cues)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/rebuild_srt_from_groups.py <task dir>")
        return 2
    task_dir = Path(sys.argv[1]).expanduser().resolve()
    count = rebuild(task_dir)
    print(f"{count} cues written to {task_dir / 'result.srt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
