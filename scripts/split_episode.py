"""Episode splitter: detect title cards in a multi-story video and cut it.

A downloaded Doraemon episode file (e.g. ``935.mp4``) usually contains two
stories (rarely three), each starting with a static title card held for ~6s.
This tool finds those cards WITHOUT OCR/AI, purely from vision statistics:

  1. Low-frequency scan (one sample every --interval seconds, default 0.5s),
     each sample reduced to a tiny grayscale signature.
  2. Find runs where consecutive samples stay highly similar (a long static
     shot) for at least --min-duration seconds.
  3. Reject runs that are black / near-solid color (fades, eyecatches).
  4. Check the run is entered/exited via an abrupt scene change.
  5. Refine each surviving run frame-by-frame (+-2s padding): the card's exact
     first/last frame is the strongest discontinuity (hard cut) on each side.
  6. Split points = the START of every title card except the first one
     (the first card belongs to story #1).
  7. Cut with ffmpeg (frame-accurate re-encode by default; --copy for fast
     keyframe-aligned stream copy).

Usage:
    uv run python scripts/split_episode.py input/935.mp4 --preview   # detect + report + images only
    uv run python scripts/split_episode.py input/935.mp4             # detect + cut + write 935_split.json
    uv run python scripts/split_episode.py input/935.mp4 -o output --episodes 2 --copy

First run on a new source should always be --preview: it writes
``<output>/preview/candidate_XX.jpg`` and ``contact_sheet.jpg`` so you can
eyeball that the candidates really are title cards before cutting anything.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cv2
import numpy as np

# --- tuning knobs (all overridable / verified via --preview) -----------------
SIG_SIZE = (96, 54)  # tiny grayscale signature for frame comparison
STABLE_SIM = 0.98  # consecutive-sample similarity that counts as "static"
MEAN_SIM = 0.978  # a qualifying run must average at least this
DIP_SIM = 0.95  # flicker dips below STABLE_SIM are tolerated down to this floor
MAX_DIP_RUN = 2  # ...for at most this many consecutive samples (x264 breathing)
MIN_CARD_DURATION = 4.0  # title card holds ~6s; below this it's not a card
REFINE_PAD = 2.0  # seconds to scan on each side of a candidate run
CUT_SIM = 0.9  # adjacent-frame sim below this at the boundary = hard scene cut
BLACK_MEAN = 12.0  # below -> black screen, reject
WHITE_MEAN = 243.0  # above -> washed-out solid, reject
SOLID_STD = 12.0  # below -> near-solid color frame, reject


@dataclass
class Sample:
    t: float
    sig: np.ndarray
    mean: float
    std: float


@dataclass
class Candidate:
    """A refined title-card candidate, times in seconds."""

    start: float
    end: float
    duration: float
    similarity: float  # mean consecutive-sample similarity inside the run
    abrupt_start: bool
    abrupt_end: bool
    ref_sig: np.ndarray = field(repr=False)  # signature at card middle

    @property
    def score(self) -> float:
        s = self.duration
        if self.abrupt_start:
            s *= 1.2
        if self.abrupt_end:
            s *= 1.1
        return s


def fmt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - float(np.mean(cv2.absdiff(a, b))) / 255.0


def scan_samples(cap: cv2.VideoCapture, fps: float, interval: float) -> list[Sample]:
    """Decode one frame every ``interval`` seconds (grab-skipping the rest)."""
    step = max(1, round(fps * interval))
    samples: list[Sample] = []
    idx = 0  # next frame index to be read/grabbed
    n_targets = 0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None
    while True:
        target = n_targets * step
        while idx < target:
            if not cap.grab():
                return samples
            idx += 1
        ok, frame = cap.read()
        if not ok:
            return samples
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sig = cv2.resize(gray, SIG_SIZE, interpolation=cv2.INTER_AREA)
        samples.append(Sample(t=idx / fps, sig=sig, mean=float(sig.mean()), std=float(sig.std())))
        idx += 1
        n_targets += 1
        if total and n_targets % 500 == 0:
            print(f"  扫描中... {idx}/{total} 帧", file=sys.stderr)
    return samples


def find_stable_runs(samples: list[Sample], stable_sim: float, min_duration: float) -> list[tuple[int, int, float]]:
    """Return ``(start_idx, end_idx, mean_sim)`` runs of static samples.

    Consecutive-sample similarity is the discriminator: a title card sits in
    a tight high band (~0.99) while genuinely moving content varies more and
    dips lower. x264 rate-control "breathing" on static frames causes
    isolated dips, so up to MAX_DIP_RUN consecutive samples in
    [DIP_SIM, STABLE_SIM) are tolerated inside a run; anything below DIP_SIM
    is a real scene change and ends the run. A run qualifies when it lasts
    >= min_duration and averages >= MEAN_SIM.
    """
    runs: list[tuple[int, int, float]] = []

    def close(a: int, b: int, run_sims: list[float]) -> None:
        if not run_sims:
            return
        mean_sim = sum(run_sims) / len(run_sims)
        if samples[b].t - samples[a].t >= min_duration and mean_sim >= MEAN_SIM:
            runs.append((a, b, mean_sim))

    start: int | None = None
    dip_run = 0
    run_sims: list[float] = []
    for i in range(1, len(samples)):
        sim = similarity(samples[i - 1].sig, samples[i].sig)
        if sim >= stable_sim:
            if start is None:
                start = i - 1
                run_sims = []
            run_sims.append(sim)
            dip_run = 0
        elif start is not None and sim >= DIP_SIM and dip_run < MAX_DIP_RUN:
            run_sims.append(sim)  # tolerated flicker, run continues
            dip_run += 1
        else:
            if start is not None:
                close(start, i - 1, run_sims)
                start = None
            dip_run = 0
    if start is not None:
        close(start, len(samples) - 1, run_sims)
    return runs


def is_black_or_solid(samples: list[Sample], a: int, b: int) -> bool:
    mid = samples[(a + b) // 2]
    return mid.mean < BLACK_MEAN or mid.mean > WHITE_MEAN or mid.std < SOLID_STD


def read_frame_at(cap: cv2.VideoCapture, frame_no: int) -> tuple[bool, np.ndarray | None]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
    ok, frame = cap.read()
    return (ok, frame if ok else None)


def find_cut_frame(cap: cv2.VideoCapture, fps: float, t0: float, t1: float) -> tuple[int, float]:
    """Return ``(frame, sim)`` of the largest discontinuity in ``[t0, t1]``.

    The cut frame is the FIRST frame after the transition, i.e. the first
    frame of the new shot. ``sim`` is the adjacent-frame similarity at that
    point (low = hard cut).
    """
    f0 = max(0, int(t0 * fps))
    f1 = max(f0 + 1, int(t1 * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    ok, frame = cap.read()
    if not ok:
        return f0, 1.0
    prev = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), SIG_SIZE, interpolation=cv2.INTER_AREA)
    best_f, best_sim = f0, 1.0
    for f in range(f0 + 1, f1 + 1):
        ok, frame = cap.read()
        if not ok:
            break
        sig = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), SIG_SIZE, interpolation=cv2.INTER_AREA)
        sim = similarity(prev, sig)
        if sim < best_sim:
            best_f, best_sim = f, sim
        prev = sig
    return best_f, best_sim


def refine_candidate(
    cap: cv2.VideoCapture,
    fps: float,
    samples: list[Sample],
    a: int,
    b: int,
    mean_sim: float,
    pad: float,
    min_duration: float,
) -> Candidate | None:
    """Frame-precise card boundaries via the hard cuts bracketing the card.

    Instead of matching frames against a reference (fragile under x264
    flicker), find the strongest discontinuity within +-pad of each coarse
    boundary: the card's first frame is the frame right after the incoming
    cut, its last frame is the one right before the outgoing cut. If a side
    has no abrupt transition (fade, or card touches the video edge), fall
    back to the coarse 0.5s-resolution boundary.
    """
    approx_start, approx_end = samples[a].t, samples[b].t

    cut_in, sim_in = find_cut_frame(cap, fps, approx_start - pad, approx_start + 1.0)
    cut_out, sim_out = find_cut_frame(cap, fps, approx_end - 1.0, approx_end + pad)

    abrupt_start = sim_in < CUT_SIM
    abrupt_end = sim_out < CUT_SIM
    start_f = cut_in if abrupt_start else round(approx_start * fps)
    end_f = cut_out if abrupt_end else round(approx_end * fps)  # exclusive
    if end_f <= start_f:
        return None
    duration = (end_f - start_f) / fps
    if duration < min_duration * 0.75:
        return None
    return Candidate(
        start=start_f / fps,
        end=end_f / fps,
        duration=duration,
        similarity=round(mean_sim, 4),
        abrupt_start=abrupt_start,
        abrupt_end=abrupt_end,
        ref_sig=samples[(a + b) // 2].sig,
    )


def detect_title_cards(video: Path, interval: float, stable_sim: float, min_duration: float, pad: float):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frames / fps if frames else 0.0

    print(f"视频: {video.name}  fps={fps:.3f}  帧数={frames}  时长={fmt_time(duration)}")
    samples = scan_samples(cap, fps, interval)
    print(f"低频采样 {len(samples)} 点 (每 {interval}s)")

    runs = find_stable_runs(samples, stable_sim, min_duration)
    print(f"稳定区间 {len(runs)} 个 (≥{min_duration}s, 相似度≥{stable_sim})")

    cards: list[Candidate] = []
    for a, b, min_sim in runs:
        if is_black_or_solid(samples, a, b):
            print(f"  跳过黑屏/纯色区间 {fmt_time(samples[a].t)} - {fmt_time(samples[b].t)}")
            continue
        cand = refine_candidate(cap, fps, samples, a, b, min_sim, pad, min_duration)
        if cand is None:
            print(f"  精修失败(边界异常) {fmt_time(samples[a].t)} - {fmt_time(samples[b].t)}")
            continue
        cards.append(cand)

    # Merge candidates that refined into overlapping regions (same card found twice)
    cards.sort(key=lambda c: c.start)
    merged: list[Candidate] = []
    for c in cards:
        if merged and c.start < merged[-1].end:
            prev = merged[-1]
            if c.end > prev.end:
                prev.end = c.end
                prev.duration = prev.end - prev.start
            continue
        merged.append(c)

    cap.release()
    return fps, duration, merged


def pick_split_cards(cards: list[Candidate], episodes: int | None) -> list[Candidate]:
    """Cards that mark a NEW story. Split points = their start times.

    Without --episodes: every card except the first splits. With --episodes N:
    keep exactly N-1 split cards, preferring long cards with abrupt boundaries.
    """
    splits = cards[1:]
    if episodes is None:
        return splits
    need = episodes - 1
    if len(splits) < need:
        raise SystemExit(
            f"期望 {episodes} 集需要 {need} 个分割点，但只检测到 {len(splits)} 个候选标题卡。请先 --preview 检查。"
        )
    chosen = sorted(splits, key=lambda c: c.score, reverse=True)[:need]
    return sorted(chosen, key=lambda c: c.start)


def save_preview(video: Path, fps: float, cards: list[Candidate], preview_dir: Path) -> None:
    preview_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    thumbs: list[np.ndarray] = []
    for i, c in enumerate(cards, 1):
        ok, frame = read_frame_at(cap, int((c.start + c.end) / 2 * fps))
        if not ok or frame is None:
            continue
        out = preview_dir / f"candidate_{i:02d}.jpg"
        cv2.imwrite(str(out), frame)
        thumb = cv2.resize(frame, (320, 180))
        label = f"#{i} {fmt_time(c.start)}-{fmt_time(c.end)} ({c.duration:.2f}s)"
        bar = np.full((28, 320, 3), 32, np.uint8)
        cv2.putText(bar, label, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        thumbs.append(np.vstack([thumb, bar]))
    cap.release()
    if thumbs:
        cols = min(3, len(thumbs))
        rows_n = math.ceil(len(thumbs) / cols)
        blank = np.full_like(thumbs[0], 16)
        grid = [thumbs[i] if i < len(thumbs) else blank for i in range(rows_n * cols)]
        rows = [np.hstack(grid[r * cols : (r + 1) * cols]) for r in range(rows_n)]
        cv2.imwrite(str(preview_dir / "contact_sheet.jpg"), np.vstack(rows))
    print(f"预览图已写入 {preview_dir}/ ({len(thumbs)} 张候选 + contact_sheet.jpg)")


def cut_episodes(
    video: Path, fps: float, duration: float, split_cards: list[Candidate], out_dir: Path, copy: bool
) -> list[dict]:
    stem = video.stem
    bounds = [0.0] + [c.start for c in split_cards] + [duration]
    episodes = []
    for i in range(len(bounds) - 1):
        name = f"{stem}#{i + 1}"
        episodes.append({"name": name, "start": round(bounds[i], 3), "end": round(bounds[i + 1], 3)})
    if not shutil.which("ffmpeg"):
        raise SystemExit("找不到 ffmpeg，无法切割。请先安装 ffmpeg。")
    for ep in episodes:
        out = out_dir / f"{ep['name']}.mp4"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ep['start']:.3f}", "-i", str(video)]
        if copy:
            cmd += ["-t", f"{ep['end'] - ep['start']:.3f}", "-c", "copy"]
        else:
            cmd += [
                "-t",
                f"{ep['end'] - ep['start']:.3f}",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        cmd += [str(out)]
        print(f"  切割 {ep['name']}: {fmt_time(ep['start'])} -> {fmt_time(ep['end'])}")
        subprocess.run(cmd, check=True)
    return episodes


def main() -> int:
    p = argparse.ArgumentParser(description="检测标题卡并把多故事视频切成单集")
    p.add_argument("video", type=Path, help="输入视频, 如 input/935.mp4")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("output"), help="输出目录 (默认 output/)")
    p.add_argument("--preview", action="store_true", help="只检测并生成预览图/报告, 不切割")
    p.add_argument("--episodes", type=int, default=None, help="期望集数 (默认: 自动, 每个标题卡开新一集)")
    p.add_argument("--interval", type=float, default=0.5, help="低频扫描间隔秒 (默认 0.5)")
    p.add_argument("--sim", type=float, default=STABLE_SIM, help=f"稳定判定相似度 (默认 {STABLE_SIM})")
    p.add_argument(
        "--min-duration", type=float, default=MIN_CARD_DURATION, help=f"标题卡最短持续秒 (默认 {MIN_CARD_DURATION})"
    )
    p.add_argument("--pad", type=float, default=REFINE_PAD, help=f"精修扫描前后扩展秒 (默认 {REFINE_PAD})")
    p.add_argument("--copy", action="store_true", help="ffmpeg 流拷贝切割 (快, 但按关键帧对齐, 不精确)")
    args = p.parse_args()

    if not args.video.is_file():
        raise SystemExit(f"文件不存在: {args.video}")

    fps, duration, cards = detect_title_cards(args.video, args.interval, args.sim, args.min_duration, args.pad)

    print("\n检测到候选:")
    for i, c in enumerate(cards, 1):
        flags = []
        if c.abrupt_start:
            flags.append("硬切入场")
        if c.abrupt_end:
            flags.append("硬切出场")
        print(f"\n[{i}] {fmt_time(c.start)} - {fmt_time(c.end)}")
        print(f"    相似度: {c.similarity}")
        print(f"    持续: {c.duration:.3f}s   {' / '.join(flags)}")

    if len(cards) < 2:
        print("\n⚠️ 检测到的标题卡不足 2 个, 无法分割。可降低 --sim / --min-duration 后用 --preview 重试。")
        return 1

    split_cards = pick_split_cards(cards, args.episodes)
    n_ep = len(split_cards) + 1
    print("\n建议分割点:")
    for c in split_cards:
        print(f"  {fmt_time(c.start)}")
    print(f"将切成 {n_ep} 集" + (" (检测到 >2 个标题卡, 注意确认是否真有 3 个故事)" if n_ep > 2 else ""))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.preview:
        save_preview(args.video, fps, cards, args.output_dir / "preview")

    bounds = [0.0] + [c.start for c in split_cards] + [duration]
    result = {
        "source": args.video.name,
        "fps": round(fps, 3),
        "duration": round(duration, 3),
        "episodes": [
            {"name": f"{args.video.stem}#{i + 1}", "start": round(bounds[i], 3), "end": round(bounds[i + 1], 3)}
            for i in range(len(bounds) - 1)
        ],
        "title_cards": [
            {
                "start": round(c.start, 3),
                "end": round(c.end, 3),
                "similarity": c.similarity,
                "abrupt_start": c.abrupt_start,
                "abrupt_end": c.abrupt_end,
            }
            for c in cards
        ],
    }
    json_path = args.output_dir / f"{args.video.stem}_split.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n检测结果已写入 {json_path}")

    if args.preview:
        print("预览模式, 未切割。确认候选无误后去掉 --preview 重新运行。")
        return 0

    if n_ep > 3:
        print("⚠️ 集数超过 3, 很可能是误检。已跳过切割, 请先 --preview 确认。")
        return 1
    cut_episodes(args.video, fps, duration, split_cards, args.output_dir, args.copy)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
