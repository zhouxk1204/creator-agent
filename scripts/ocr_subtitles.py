"""OCR the burned-in (hard-coded) captions of a video's frames.

Complements the ASR transcript: ASR captures *what is said*, this captures
*what is drawn on the picture* - Douyin creators usually burn the same captions
onto the video, and those captions are the clean, correctly-spelled text.

Runs in the ``qwen3vl`` conda env (needs rapidocr-onnxruntime + opencv + numpy;
the main uv env does not carry those)::

    C:/Users/34696/miniconda3/envs/qwen3vl/python.exe scripts/ocr_subtitles.py \
        storage/_adhoc/<video_id>/video.mp4 [--fps 2] [--min-score 0.6] [--from-json]

``--from-json`` re-merges the cached ``ocr_lines.json`` without re-running OCR,
which is what you want when tuning the merge step.

Writes next to the video:
    ocr_lines.json      every sampled frame's OCR result, with timestamps
    ocr_subtitles.txt   merged captions (static title/watermark text removed)
    ocr_subtitles.srt   the same with timecodes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import cv2
from rapidocr_onnxruntime import RapidOCR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _clean(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text)


def _is_noise(text: str) -> bool:
    """Drop avatar counter / watermark fragments such as ``文117`` or ``11``."""
    cleaned = _clean(text)
    return not cleaned or bool(re.fullmatch(r"文?\d*", cleaned))


def _static_texts(frames: list[dict], threshold: float = 0.6) -> set[str]:
    """Text present on nearly every frame (title banner, watermark, handle)."""
    counts: Counter[str] = Counter()
    for frame in frames:
        for text in {_normalize(i["text"]) for i in frame["lines"]}:
            counts[text] += 1
    minimum = max(4, int(threshold * len(frames)))
    return {text for text, count in counts.items() if count >= minimum}


def _infer_gap(frames: list[dict], fallback: float) -> float:
    stamps = [f["t"] for f in frames]
    deltas = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    return min(deltas) if deltas else fallback


def _srt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def ocr_frame(engine: RapidOCR, frame, min_score: float) -> list[dict]:
    """OCR one BGR frame; boxes come back sorted top-to-bottom, left-to-right."""
    result, _ = engine(frame)
    items: list[dict] = []
    for box, text, score in result or []:
        if score is None or score < min_score or not text.strip():
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append(
            {
                "text": text.strip(),
                "score": round(float(score), 3),
                "x": round(min(xs), 1),
                "y": round(min(ys), 1),
            }
        )
    items.sort(key=lambda i: (round(i["y"] / 20), i["x"]))
    return items


def _visible_lines(frame: dict, static: set[str]) -> list[dict]:
    return [i for i in frame["lines"] if not _is_noise(i["text"]) and _normalize(i["text"]) not in static]


def merge_captions(frames: list[dict], static: set[str], gap_sec: float) -> list[dict]:
    """Collapse per-frame OCR into one entry per caption that stayed on screen."""
    merged: list[dict] = []
    for frame in frames:
        text = " ".join(i["text"] for i in _visible_lines(frame, static))
        key = _normalize(text)
        if not key:
            continue
        if merged and key == merged[-1]["key"]:
            merged[-1]["end"] = frame["t"]
            continue
        if merged and key in merged[-1]["key"]:
            # Transition frame: the outgoing caption is still partly on screen.
            continue
        merged.append({"key": key, "text": text, "start": frame["t"], "end": frame["t"]})
    for entry in merged:
        entry["end"] += gap_sec
        entry.pop("key")
    return merged


def _similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """True when two captions share enough text to be the same caption."""
    if not a or not b:
        return False
    match = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return match.size / min(len(a), len(b)) >= threshold


def group_captions(captions: list[dict], threshold: float = 0.5) -> list[dict]:
    """Collapse the frames of one caption (typed/rolled in) into a single entry.

    A caption is revealed with an animation, so consecutive frames differ by a
    few characters. Such frames share a long common substring, whereas the next
    quote does not. The longest variant is kept as the canonical text.
    """
    groups: list[dict] = []
    for caption in captions:
        key = _normalize(caption["text"])
        target = next((g for g in groups if any(_similar(key, k, threshold) for k in g["keys"])), None)
        if target is None:
            groups.append({"keys": [key], "text": caption["text"], "start": caption["start"], "end": caption["end"]})
            continue
        target["keys"].append(key)
        target["end"] = caption["end"]
        if len(caption["text"]) > len(target["text"]):
            target["text"] = caption["text"]
    return [{"text": g["text"], "start": g["start"], "end": g["end"]} for g in groups]


def _ocr_video(args, video_path: Path) -> tuple[list[dict], float]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(native_fps / args.fps)))
    print(f"Video: {video_path.name}  {native_fps:.2f} fps, {frame_count} frames -> sampling every {step}")

    engine = RapidOCR()
    frames: list[dict] = []
    index = 0
    while True:
        if not cap.grab():
            break
        if index % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = index / native_fps
            lines = ocr_frame(engine, frame, args.min_score)
            frames.append({"t": round(t, 3), "lines": lines})
            print(f"  [{t:6.2f}s] {' | '.join(i['text'] for i in lines)[:100]}")
        index += 1
    cap.release()
    return frames, 1.0 / args.fps


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR burned-in captions from a video.")
    parser.add_argument("video", help="Path to the video file")
    parser.add_argument("--fps", type=float, default=2.0, help="Frames per second to sample (default 2)")
    parser.add_argument("--min-score", type=float, default=0.6, help="Drop OCR results below this score")
    parser.add_argument(
        "--from-json",
        action="store_true",
        help="Re-merge the existing ocr_lines.json instead of OCR-ing the video again",
    )
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"Video not found: {video_path}")
        return 2
    out_dir = video_path.parent
    lines_path = out_dir / "ocr_lines.json"

    if args.from_json:
        if not lines_path.exists():
            print(f"No OCR cache to re-merge: {lines_path}")
            return 2
        frames = json.loads(lines_path.read_text(encoding="utf-8"))
        gap = _infer_gap(frames, 1.0 / args.fps)
        print(f"Re-merging {len(frames)} cached frame result(s) from {lines_path.name}")
    else:
        frames, gap = _ocr_video(args, video_path)
        lines_path.write_text(json.dumps(frames, ensure_ascii=False, indent=2), encoding="utf-8")

    static = _static_texts(frames)
    captions = merge_captions(frames, static, gap)
    raw_count = len(captions)
    captions = group_captions(captions)
    (out_dir / "ocr_subtitles.txt").write_text("\n".join(c["text"] for c in captions), encoding="utf-8")
    srt = [
        f"{n}\n{_srt_time(c['start'])} --> {_srt_time(c['end'])}\n{c['text']}\n"
        for n, c in enumerate(captions, start=1)
    ]
    (out_dir / "ocr_subtitles.srt").write_text("\n".join(srt), encoding="utf-8")

    print(f"\nIgnored static overlay text ({len(static)}): {' / '.join(sorted(static)) or '(none)'}")
    print(f"DONE. {raw_count} frame caption(s) -> {len(captions)} caption(s) -> {out_dir / 'ocr_subtitles.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
