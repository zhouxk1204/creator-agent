"""Print a readable summary of one subtitle-extractor task directory."""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: subtitle_srt_report.py <task-output-dir>")
        return 2
    root = Path(sys.argv[1]).expanduser().resolve()
    result_file = root / "result.json"
    if not result_file.is_file():
        print(f"[X] No result.json in {root}")
        status_file = root / "task_status.json"
        if status_file.is_file():
            status = json.loads(status_file.read_text(encoding="utf-8")).get("status")
            print(f"    task_status.json says: {status}")
        return 1

    result = json.loads(result_file.read_text(encoding="utf-8"))
    summary = result["summary"]
    srt_file = root / result.get("srt", "result.srt")
    cues: list[str] = []
    if srt_file.is_file():
        cues = [block for block in srt_file.read_text(encoding="utf-8").strip().split("\n\n") if block.strip()]

    print()
    print("=" * 62)
    print(" Subtitle extraction summary")
    print("=" * 62)
    print(f"  frames processed   : {summary['processed_frames']}")
    print(f"  frames with text   : {summary['detected_frames']} ({summary['detection_rate'] * 100:.1f}%)")
    print(f"  subtitle groups    : {summary['groups']} (merged {summary['merged_groups']})")
    print(f"  OCR calls          : {summary['ocr_calls']} (failures {summary['ocr_failures']})")
    print(f"  SRT cues           : {summary['srt_cues']}")
    if cues:
        total = 0.0
        for block in cues:
            span = block.split("\n")[1]
            start, end = span.split(" --> ")
            total += _seconds(end) - _seconds(start)
        print(f"  subtitle coverage  : {total:.1f}s")
    print(f"  SRT file           : {srt_file}")
    print(f"  Task folder        : {root}")
    print()

    if cues:
        print("--- first 5 cues ---")
        for block in cues[:5]:
            print(_format(block))
        print("--- last 3 cues ---")
        for block in cues[-3:]:
            print(_format(block))
    else:
        print("[!] result.srt is empty - check groups/groups.json and debug/ images.")
    return 0


def _seconds(stamp: str) -> float:
    hours, minutes, rest = stamp.split(":")
    secs, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(millis) / 1000


def _format(block: str) -> str:
    lines = block.split("\n")
    return f"  #{lines[0]:>3}  {lines[1]}  | {lines[2] if len(lines) > 2 else ''}"


if __name__ == "__main__":
    raise SystemExit(main())
