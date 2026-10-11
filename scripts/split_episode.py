"""Episode splitter: detect title cards in a multi-story video and cut it.

A downloaded Doraemon episode file (e.g. ``935.mp4``) usually contains two
stories (rarely three), each starting with a static title card held for ~6s.
This tool finds those cards WITHOUT OCR/AI, purely from vision statistics:

  1. Low-frequency scan (one sample every --interval seconds, default 0.5s),
     each sample reduced to a tiny grayscale signature.
  2. Find runs where every sample matches the run's FIRST sample (the anchor)
     at similarity >= --sim (default 0.999). A title card is a still image, so
     every frame is near-identical to the anchor; ordinary "static" anime
     shots (held cels, talking heads) still drift well below 0.999 over a
     multi-second run and are rejected. Comparing consecutive samples instead
     (the old approach) lets any slow-moving shot through.
  3. Reject runs that are black / near-solid color (fades, eyecatches).
  4. Check the run is entered/exited via an abrupt scene change.
  5. Refine each surviving run frame-by-frame (+-2s padding): the card's exact
     first/last frame is the strongest discontinuity (hard cut) on each side.
  6. Verify the card actually shows BIG TITLE TEXT (no OCR): on the card's
     middle frame, MSER components of character size are clustered into
     baseline-aligned lines; a title line is >=4 same-size stroke-dense
     components in the upper part of the frame. Static shots without title
     text (post-story bumpers, sponsor stills) are dropped here; in
     --preview they are kept but labelled NOTEXT. --no-text-filter disables.
  7. Split points = the START of every title card INCLUDING the first one:
     story #1 begins at its card, and the OP/intro before it is discarded
     (--keep-intro keeps the old behavior of starting story #1 at 0:00).
  8. Output names come from the episode titles embedded in the filename:
     「ドラえもん 「A」「B」.mp4」 -> A.mp4 / B.mp4; falls back to <stem>#N
     when the title count doesn't match the episode count.
  9. Snap each story's END to the nearest black frame: for non-last stories
     the last black run (>=0.15s) in the [card-60s, card-20s] window before
     the next title card — a black run closer to the card is its transition-
     in, not the story end (cutting there keeps sponsor/eyecatch junk); for
     the last story the last black run in the final 180s. If no black run is
     found the original boundary is kept. END rows in the contact sheet show
     the frame right before the aligned black run (end_XX.jpg too).
 10. Cut with ffmpeg (frame-accurate re-encode by default; --copy for fast
     keyframe-aligned stream copy). The re-encode uses a GPU encoder
     (nvenc/qsv/amf/videotoolbox) when one actually works on this machine —
     verified with a tiny probe encode — else falls back to libx264 (--cpu
     forces CPU). Frame decoding for detection likewise prefers hardware
     acceleration via OpenCV's FFmpeg backend (D3D11/DXVA on Windows).

Usage:
    uv run python scripts/split_episode.py input/935.mp4 --preview   # detect + report + images only
    uv run python scripts/split_episode.py input/935.mp4             # detect + cut + write 935_split.json
    uv run python scripts/split_episode.py input/935.mp4 --confirm   # detect + show contact sheet, y/n -> cut
    uv run python scripts/split_episode.py input/935.mp4 -o output --episodes 2 --copy

First run on a new source should always be --preview: it writes
``<output>/preview/candidate_XX.jpg`` and ``contact_sheet.jpg`` so you can
eyeball that the candidates really are title cards before cutting anything.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
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
ANCHOR_SIM = 0.999  # every sample in a card run matches the run's first frame this closely
DIP_SIM = 0.995  # encoder "breathing" dips below ANCHOR_SIM tolerated down to this floor
MAX_DIP_RUN = 2  # ...for at most this many consecutive samples
MIN_CARD_DURATION = 5.0  # title card holds ~6s; ordinary static shots rarely hold 5s
REFINE_PAD = 2.0  # seconds to scan on each side of a candidate run
CUT_SIM = 0.9  # adjacent-frame sim below this at the boundary = hard scene cut
BLACK_MEAN = 12.0  # below -> black screen, reject
WHITE_MEAN = 243.0  # above -> washed-out solid, reject
SOLID_STD = 12.0  # below -> near-solid color frame, reject
# --- title-text verification (step 6; all fractions of frame height) --------
TEXT_CHAR_H_MIN = 0.03  # MSER component height range for "character" candidates
TEXT_CHAR_H_MAX = 0.25
TEXT_LINE_MIN = 4  # a title line needs at least this many same-size components
TEXT_DENSITY_MIN = 0.055  # mean interior stroke density of a real text line
TEXT_CY_MAX = 0.45  # title text sits in the upper part of the card
TEXT_LINE_H_MIN = 0.12  # median char height of the TITLE line: real titles are
# ~0.17H; corner logos / mini-corner captions are ~0.08H and must not count
# --- episode end snapping (step 9) -------------------------------------------
# Non-last stories: the end black run must sit in [card-60s, card-20s].
# A black run CLOSER than 20s to the next title card is the card's own
# transition-in, not the story end (cutting there keeps sponsor/eyecatch
# interstitials); further than 60s is mid-story flicker.
END_BLACK_BEFORE_CARD_MIN = 20.0
END_BLACK_BEFORE_CARD_MAX = 60.0
END_BLACK_WINDOW_LAST = 180.0  # last story: search this tail window
END_BLACK_MIN_RUN = 0.15  # ignore sub-0.15s black flickers
# End preview frame (contact sheet END rows / end_XX.jpg): the literal frame
# before the black run is often a dark fade-out tail (mean barely above the
# black threshold), which is uninformative to eyeball. Walk back up to 2s to
# the last clearly visible content frame instead. The CUT point is unaffected.
END_PREVIEW_MIN_MEAN = 35.0
END_PREVIEW_MAX_BACKTRACK = 2.0


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
    similarity: float  # min similarity vs the run's anchor frame
    abrupt_start: bool
    abrupt_end: bool
    ref_sig: np.ndarray = field(repr=False)  # signature at card middle
    has_title_text: bool = True  # step-6 check; default keeps hand-built cards

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


def open_capture(video: Path) -> cv2.VideoCapture:
    """Open the video, preferring GPU (hardware) decode when available.

    OpenCV's FFmpeg backend can offload H.264 decode to the GPU (D3D11/DXVA
    on Windows). Probe one frame; if hardware decode fails for any reason,
    fall back to plain software decoding.
    """
    try:
        cap = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG, [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY])
        if cap.isOpened() and cap.grab():
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            return cap
        cap.release()
    except Exception:
        pass
    return cv2.VideoCapture(str(video))


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


def find_stable_runs(samples: list[Sample], anchor_sim: float, min_duration: float) -> list[tuple[int, int, float]]:
    """Return ``(start_idx, end_idx, min_anchor_sim)`` runs of static samples.

    Every sample in a run is compared against the run's FIRST sample (the
    anchor), not its predecessor: a title card is a still image, so all its
    frames sit in a tight band (~0.9995+) against the anchor, while ordinary
    static-looking anime shots (held cels, blinking characters, background
    scroll) drift away from the anchor over seconds even though each
    consecutive pair looks similar. x264 rate-control "breathing" causes
    isolated dips, so up to MAX_DIP_RUN consecutive samples in
    [DIP_SIM, anchor_sim) are tolerated without ending the run (they just
    don't count toward min_anchor_sim). A run qualifies when it lasts
    >= min_duration.
    """
    runs: list[tuple[int, int, float]] = []
    start: int | None = None
    min_sim = 1.0
    dip_run = 0

    def close(a: int, b: int) -> None:
        if samples[b].t - samples[a].t >= min_duration:
            runs.append((a, b, min_sim))

    for i in range(1, len(samples)):
        if start is None:
            # a run opens on a near-identical consecutive pair
            if similarity(samples[i - 1].sig, samples[i].sig) >= anchor_sim:
                start = i - 1
                min_sim = 1.0
                dip_run = 0
            continue
        sim = similarity(samples[start].sig, samples[i].sig)
        if sim >= anchor_sim:
            min_sim = min(min_sim, sim)
            dip_run = 0
        elif sim >= DIP_SIM and dip_run < MAX_DIP_RUN:
            dip_run += 1  # tolerated breathing; run continues
        else:
            close(start, i - 1)
            start = None
            dip_run = 0
    if start is not None:
        close(start, len(samples) - 1)
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
    anchor_sim_min: float,
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
        similarity=round(anchor_sim_min, 4),
        abrupt_start=abrupt_start,
        abrupt_end=abrupt_end,
        ref_sig=samples[(a + b) // 2].sig,
    )


def _mser_char_boxes(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Character-size MSER components (both polarities), IoU-deduped."""
    h, w = gray.shape
    boxes = []
    for g in (gray, 255 - gray):
        mser = cv2.MSER_create(delta=5, min_area=int(gray.size * 0.00008), max_area=int(gray.size * 0.02))
        _, bbs = mser.detectRegions(g)
        boxes.extend(map(tuple, bbs))
    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple[int, int, int, int]] = []
    for b in boxes:
        x, y, bw, bh = b
        if not (TEXT_CHAR_H_MIN <= bh / h <= TEXT_CHAR_H_MAX and 0.12 <= bw / bh <= 1.5):
            continue
        if any(
            (max(0, min(x + bw, X + W) - max(x, X)) * max(0, min(y + bh, Y + H) - max(y, Y)))
            > 0.65 * min(bw * bh, W * H)
            for (X, Y, W, H) in kept
        ):
            continue
        kept.append(b)
    return kept


def _baseline_lines(boxes: list[tuple[int, int, int, int]]) -> list[list[tuple[int, int, int, int]]]:
    """Cluster char boxes into baseline-aligned lines (union-find).

    Text line = >=TEXT_LINE_MIN components of similar height whose y-ranges
    overlap, centers fit one baseline, and x-gaps stay small. Background junk
    (foliage, character art) is random in size/position and fails this.
    """
    n = len(boxes)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        xi, yi, wi, hi = boxes[i]
        for j in range(i + 1, n):
            xj, yj, wj, hj = boxes[j]
            if abs(hi - hj) > 0.35 * min(hi, hj):
                continue
            if min(yi + hi, yj + hj) - max(yi, yj) < 0.45 * min(hi, hj):
                continue
            parent[find(i)] = find(j)

    groups: dict[int, list] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(boxes[i])

    lines: list[list[tuple[int, int, int, int]]] = []
    for g in groups.values():
        if len(g) < TEXT_LINE_MIN:
            continue
        g.sort(key=lambda b: b[0])
        med_h = float(np.median([b[3] for b in g]))
        med_w = float(np.median([b[2] for b in g]))
        if np.array([b[1] + b[3] / 2 for b in g]).std() > 0.55 * med_h:
            continue
        seg = [g[0]]
        for prev, b in zip(g, g[1:]):
            if b[0] - (prev[0] + prev[2]) > 2.5 * med_w:
                if len(seg) >= TEXT_LINE_MIN:
                    lines.append(seg)
                seg = [b]
            else:
                seg.append(b)
        if len(seg) >= TEXT_LINE_MIN:
            lines.append(seg)
    return lines


def _interior_stroke_density(edges: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """Edge density inside a box EXCLUDING a boundary ring.

    Text strokes criss-cross the character interior; a blob's outline only
    lives at its boundary, so blob interiors are nearly edge-free.
    """
    x, y, w, h = box
    r = int(min(w, h) * 0.18)
    xi0, yi0, xi1, yi1 = x + r, y + r, x + w - r, y + h - r
    if xi1 <= xi0 or yi1 <= yi0:
        return 0.0
    roi = edges[yi0:yi1, xi0:xi1]
    return float(np.count_nonzero(roi)) / roi.size


def has_title_text(frame: np.ndarray) -> bool:
    """True if the frame shows a line of BIG TITLE TEXT (see docstring step 6)."""
    h = frame.shape[0]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 60, 160)
    for line in _baseline_lines(_mser_char_boxes(gray)):
        cy = float(np.mean([b[1] + b[3] / 2 for b in line])) / h
        if cy > TEXT_CY_MAX:
            continue
        med_h = float(np.median([b[3] for b in line])) / h
        if med_h < TEXT_LINE_H_MIN:
            continue
        density = float(np.mean([_interior_stroke_density(edges, b) for b in line]))
        if density >= TEXT_DENSITY_MIN:
            return True
    return False


def find_black_run_end(cap: cv2.VideoCapture, fps: float, t0: float, t1: float) -> int | None:
    """Frame index of the LAST black run's first frame (>=END_BLACK_MIN_RUN)
    in the window [t0, t1].

    A story's true content ends where the black gap before the interstitial
    (or the video's trailing black) begins; the run start is the cut point,
    so the black itself is excluded.
    """
    f1 = int(t1 * fps)
    f0 = max(0, int(t0 * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    last_start: int | None = None
    run_start: int | None = None
    f = f0
    while f <= f1:
        ok, frame = cap.read()
        if not ok:
            break
        sig = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), SIG_SIZE, interpolation=cv2.INTER_AREA)
        if float(sig.mean()) < BLACK_MEAN:
            if run_start is None:
                run_start = f
        else:
            if run_start is not None and (f - run_start) / fps >= END_BLACK_MIN_RUN:
                last_start = run_start
            run_start = None
        f += 1
    if run_start is not None and (f - run_start) / fps >= END_BLACK_MIN_RUN:
        last_start = run_start
    return last_start


def adjust_episode_ends(video: Path, fps: float, episodes: list[dict]) -> None:
    """Snap each episode's end to the nearest black frame (step 9). Mutates ``episodes``:
    sets ``end`` (seconds) and ``end_frame`` (exact black-run start index, used
    for the end preview so no float-rounding can shift it off by a frame)."""
    cap = open_capture(video)
    try:
        for i, ep in enumerate(episodes):
            if i < len(episodes) - 1:
                t0, t1 = ep["end"] - END_BLACK_BEFORE_CARD_MAX, ep["end"] - END_BLACK_BEFORE_CARD_MIN
            else:
                t0, t1 = ep["end"] - END_BLACK_WINDOW_LAST, ep["end"]
            black_f = find_black_run_end(cap, fps, t0, t1)
            if black_f is None:
                print(f"  {ep['name']} 结尾窗口内未找到黑帧，保持原结尾 {fmt_time(ep['end'])}")
                continue
            black = black_f / fps
            if black <= ep["start"] + 1.0:
                print(f"  {ep['name']} 黑帧异常(早于故事起点)，保持原结尾 {fmt_time(ep['end'])}")
            elif black < ep["end"] - 0.05:
                print(f"  结尾黑帧对齐 {ep['name']}: {fmt_time(ep['end'])} -> {fmt_time(black)}")
                ep["end"] = round(black, 3)
                ep["end_frame"] = black_f
    finally:
        cap.release()


def detect_title_cards(
    video: Path, interval: float, anchor_sim: float, min_duration: float, pad: float, text_filter: bool = True
):
    cap = open_capture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frames / fps if frames else 0.0

    print(f"视频: {video.name}  fps={fps:.3f}  帧数={frames}  时长={fmt_time(duration)}")
    samples = scan_samples(cap, fps, interval)
    print(f"低频采样 {len(samples)} 点 (每 {interval}s)")

    runs = find_stable_runs(samples, anchor_sim, min_duration)
    print(f"稳定区间 {len(runs)} 个 (≥{min_duration}s, 锚定相似度≥{anchor_sim})")

    cards: list[Candidate] = []
    for a, b, min_sim in runs:
        if is_black_or_solid(samples, a, b):
            print(f"  跳过黑屏/纯色区间 {fmt_time(samples[a].t)} - {fmt_time(samples[b].t)}")
            continue
        cand = refine_candidate(cap, fps, samples, a, b, min_sim, pad, min_duration)
        if cand is None:
            print(f"  精修失败(边界异常) {fmt_time(samples[a].t)} - {fmt_time(samples[b].t)}")
            continue
        ok, frame = read_frame_at(cap, int((cand.start + cand.end) / 2 * fps))
        cand.has_title_text = bool(ok and frame is not None and has_title_text(frame))
        if not cand.has_title_text:
            print(f"  无大标题文字 {fmt_time(cand.start)} - {fmt_time(cand.end)}", end="")
            if text_filter:
                print(" -> 丢弃 (--no-text-filter 可关闭)")
                continue
            print(" -> 保留 (text_filter off)")
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


def pick_split_cards(cards: list[Candidate], n_splits: int | None) -> list[Candidate]:
    """Cards that open a NEW story. Split points = their start times.

    Without --episodes: every candidate card splits. With n_splits=N: keep
    exactly N split cards, preferring long cards with abrupt boundaries.
    """
    if n_splits is None:
        return cards
    if len(cards) < n_splits:
        raise SystemExit(f"期望 {n_splits} 个分割点，但只检测到 {len(cards)} 个候选标题卡。请先 --preview 检查。")
    chosen = sorted(cards, key=lambda c: c.score, reverse=True)[:n_splits]
    return sorted(chosen, key=lambda c: c.start)


def parse_episode_titles(stem: str) -> list[str]:
    """Episode titles embedded in the filename: ドラえもん 「A」「B」 -> [A, B]."""
    return re.findall(r"「([^」]+)」", stem)


def episode_names(stem: str, n: int) -> list[str]:
    """Output basenames for n episodes, preferring the 「titles」 in the filename."""
    titles = parse_episode_titles(stem)
    if len(titles) == n:
        return [re.sub(r'[<>:"/\\|?*]', "_", t).strip() for t in titles]
    if titles:
        print(f"⚠️ 文件名含 {len(titles)} 个「标题」但切成 {n} 集，回退为 {stem}#N 命名", file=sys.stderr)
    return [f"{stem}#{i + 1}" for i in range(n)]


def _labelled_thumb(frame: np.ndarray, label: str) -> np.ndarray:
    thumb = cv2.resize(frame, (320, 180))
    bar = np.full((28, 320, 3), 32, np.uint8)
    cv2.putText(bar, label, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([thumb, bar])


def _end_preview_frame(cap: cv2.VideoCapture, fps: float, frame_no: int) -> np.ndarray | None:
    """Preview image for an episode end: the last clearly visible content
    frame at or before ``frame_no`` (the frame right before the black run).

    Stories often FADE to black, so the literal frame before the run is a
    near-black fade tail; walk back up to END_PREVIEW_MAX_BACKTRACK seconds
    for the last frame at END_PREVIEW_MIN_MEAN or brighter. Falls back to the
    literal frame if everything nearby is dark.
    """
    f0 = max(0, frame_no - int(END_PREVIEW_MAX_BACKTRACK * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    best: np.ndarray | None = None
    for _f in range(f0, frame_no + 1):
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        sig = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), SIG_SIZE, interpolation=cv2.INTER_AREA)
        if float(sig.mean()) >= END_PREVIEW_MIN_MEAN:
            best = frame
    if best is None:
        ok, best = read_frame_at(cap, frame_no)
        if not ok:
            best = None
    return best


def save_preview(
    video: Path, fps: float, cards: list[Candidate], preview_dir: Path, episodes: list[dict] | None = None
) -> None:
    """Contact sheet of candidate title cards (+ episode end frames when given).

    End thumbs show the LAST NON-BLACK frame of each story: the frame right
    before the black run the end was snapped to (or the last frame before the
    plain boundary when no black run was found).
    """
    preview_dir.mkdir(parents=True, exist_ok=True)
    cap = open_capture(video)
    thumbs: list[np.ndarray] = []
    for i, c in enumerate(cards, 1):
        ok, frame = read_frame_at(cap, int((c.start + c.end) / 2 * fps))
        if not ok or frame is None:
            continue
        out = preview_dir / f"candidate_{i:02d}.jpg"
        cv2.imwrite(str(out), frame)
        label = f"#{i} {fmt_time(c.start)}-{fmt_time(c.end)} ({c.duration:.2f}s){'' if c.has_title_text else ' NOTEXT'}"
        thumbs.append(_labelled_thumb(frame, label))
    n_ends = 0
    if episodes:
        for i, ep in enumerate(episodes, 1):
            # the frame right BEFORE the black run = the story's last content
            # frame; end_frame is the exact index (no float-rounding drift),
            # falling back to the plain boundary when no black run was found
            frame_no = ep.get("end_frame", int(ep["end"] * fps)) - 1
            frame = _end_preview_frame(cap, fps, max(0, frame_no))
            if frame is None:
                continue
            cv2.imwrite(str(preview_dir / f"end_{i:02d}.jpg"), frame)
            label = f"END {ep['name']}: last frame @ {fmt_time(ep['end'])}"
            thumbs.append(_labelled_thumb(frame, label))
            n_ends += 1
    cap.release()
    if thumbs:
        cols = min(3, len(thumbs))
        rows_n = math.ceil(len(thumbs) / cols)
        blank = np.full_like(thumbs[0], 16)
        grid = [thumbs[i] if i < len(thumbs) else blank for i in range(rows_n * cols)]
        rows = [np.hstack(grid[r * cols : (r + 1) * cols]) for r in range(rows_n)]
        cv2.imwrite(str(preview_dir / "contact_sheet.jpg"), np.vstack(rows))
    print(f"预览图已写入 {preview_dir}/ ({len(cards)} 张候选 + {n_ends} 张结尾 + contact_sheet.jpg)")


def _open_in_shell(path: Path) -> None:
    """Open a file/dir with the Windows shell (image viewer / Explorer)."""
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows-only tool
    except Exception as e:
        print(f"  无法自动打开 {path}: {e}")


def find_ffmpeg() -> str | None:
    """ffmpeg on PATH, else the one configured for ASR in settings.yaml."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        from creator_agent.config import load_settings

        path = Path(load_settings().asr.ffmpeg_path)
        if path.exists():
            return str(path)
    except Exception:
        pass
    return None


# GPU encoders tried in order; the first whose probe encode actually runs
# (driver + GPU present) wins. Quality settings roughly match libx264 crf 18.
HW_ENCODERS: list[tuple[str, list[str]]] = [
    ("h264_nvenc", ["-rc:v", "vbr", "-cq:v", "20", "-b:v", "0"]),  # NVIDIA
    ("h264_qsv", ["-global_quality", "20"]),  # Intel
    ("h264_amf", ["-quality", "balanced", "-rc", "cqp", "-qp_i", "20", "-qp_p", "20"]),  # AMD
    ("h264_videotoolbox", ["-q:v", "65"]),  # Apple
]
CPU_VARGS = ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]


def probe_encoder(ffmpeg: str, name: str, vargs: list[str]) -> bool:
    """True if a tiny test encode with ``name`` actually runs on this machine.

    ``ffmpeg -encoders`` listing an encoder doesn't mean it works (e.g. nvenc
    is compiled in but there's no NVIDIA GPU), so we really encode 5 frames.
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=10:duration=0.5",
        "-c:v",
        name,
        *vargs,
        "-pix_fmt",
        "yuv420p",
        "-frames:v",
        "5",
        "-f",
        "null",
        "-",
    ]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def choose_video_args(ffmpeg: str, force_cpu: bool) -> tuple[str, list[str]]:
    if not force_cpu:
        for name, vargs in HW_ENCODERS:
            if probe_encoder(ffmpeg, name, vargs):
                return f"{name} (GPU)", ["-c:v", name, *vargs, "-pix_fmt", "yuv420p"]
        print("未检测到可用的 GPU 编码器, 回退 CPU libx264")
    return "libx264 (CPU)", [*CPU_VARGS, "-pix_fmt", "yuv420p"]


def cut_episodes(video: Path, episodes: list[dict], out_dir: Path, copy: bool, force_cpu: bool = False) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise SystemExit("找不到 ffmpeg：PATH 上没有，settings.yaml 的 asr.ffmpeg_path 也未配置。请先安装 ffmpeg。")
    if copy:
        label, vargs = "stream copy", []
    else:
        label, vargs = choose_video_args(ffmpeg, force_cpu)
    print(f"切割编码器: {label}")
    for ep in episodes:
        out = out_dir / f"{ep['name']}.mp4"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ep['start']:.3f}", "-i", str(video)]
        if copy:
            cmd += ["-t", f"{ep['end'] - ep['start']:.3f}", "-c", "copy"]
        else:
            cmd += ["-t", f"{ep['end'] - ep['start']:.3f}", *vargs, "-c:a", "aac", "-b:a", "192k"]
        cmd += [str(out)]
        print(f"  切割 {ep['name']}: {fmt_time(ep['start'])} -> {fmt_time(ep['end'])}")
        subprocess.run(cmd, check=True)


def main() -> int:
    p = argparse.ArgumentParser(description="检测标题卡并把多故事视频切成单集")
    p.add_argument("video", type=Path, help="输入视频, 如 input/935.mp4")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("output"), help="输出目录 (默认 output/)")
    p.add_argument("--preview", action="store_true", help="只检测并生成预览图/报告, 不切割")
    p.add_argument("--episodes", type=int, default=None, help="期望集数 (默认: 自动, 每个标题卡开新一集)")
    p.add_argument("--interval", type=float, default=0.5, help="低频扫描间隔秒 (默认 0.5)")
    p.add_argument(
        "--sim", type=float, default=ANCHOR_SIM, help=f"锚定相似度: 每帧与区间首帧的最低相似度 (默认 {ANCHOR_SIM})"
    )
    p.add_argument(
        "--min-duration", type=float, default=MIN_CARD_DURATION, help=f"标题卡最短持续秒 (默认 {MIN_CARD_DURATION})"
    )
    p.add_argument("--pad", type=float, default=REFINE_PAD, help=f"精修扫描前后扩展秒 (默认 {REFINE_PAD})")
    p.add_argument("--copy", action="store_true", help="ffmpeg 流拷贝切割 (快, 但按关键帧对齐, 不精确)")
    p.add_argument("--cpu", action="store_true", help="强制 CPU (libx264) 重编码切割, 不尝试 GPU 编码器")
    p.add_argument("--keep-intro", action="store_true", help="第一集从 0:00 开始 (保留第一个标题卡前的 OP/引子)")
    p.add_argument(
        "--no-text-filter",
        action="store_true",
        help="关闭大标题文字校验 (默认丢弃没有标题文字的静态候选; --preview 下始终只标注不丢弃)",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="切割前打开标题卡联系表并在控制台等待 y/n 确认 (取消时退出码 2 并打开预览目录)",
    )
    args = p.parse_args()

    if not args.video.is_file():
        raise SystemExit(f"文件不存在: {args.video}")

    # Preview keeps NOTEXT candidates (labelled) so nothing is hidden; cut
    # mode drops them unless --no-text-filter.
    text_filter = not args.no_text_filter and not args.preview
    fps, duration, cards = detect_title_cards(
        args.video, args.interval, args.sim, args.min_duration, args.pad, text_filter=text_filter
    )

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
        if not c.has_title_text:
            print("    ⚠️ 未检测到大标题文字")

    if not cards:
        print("\n⚠️ 没有检测到标题卡, 无法分割。可降低 --sim / --min-duration 后用 --preview 重试。")
        print("   例如: --sim 0.997 或 --min-duration 4")
        if text_filter:
            print("   若候选都因无大标题文字被丢弃，可用 --no-text-filter 关闭该校验再试。")
        return 1

    # Default: every card opens a new story, story #1 starts at the first card
    # and the OP/intro before it is discarded. --keep-intro keeps story #1
    # from 0:00 and splits only at cards[1:].
    if args.keep_intro:
        n_splits = None if args.episodes is None else args.episodes - 1
        split_cards = pick_split_cards(cards[1:], n_splits)
        bounds = [0.0] + [c.start for c in split_cards] + [duration]
    else:
        split_cards = pick_split_cards(cards, args.episodes)
        bounds = [c.start for c in split_cards] + [duration]

    n_ep = len(bounds) - 1
    names = episode_names(args.video.stem, n_ep)
    episodes = [{"name": names[i], "start": round(bounds[i], 3), "end": round(bounds[i + 1], 3)} for i in range(n_ep)]

    print("\n结尾黑帧对齐:")
    adjust_episode_ends(args.video, fps, episodes)

    print("\n建议分割方案:")
    for ep in episodes:
        print(f"  {ep['name']}: {fmt_time(ep['start'])} -> {fmt_time(ep['end'])}")
    if not args.keep_intro and split_cards:
        print(f"(已舍弃 {fmt_time(split_cards[0].start)} 之前的 OP/引子部分)")
    print(f"将切成 {n_ep} 集" + (" (检测到 >2 个标题卡, 注意确认是否真有 3 个故事)" if n_ep > 2 else ""))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.preview:
        save_preview(args.video, fps, cards, args.output_dir / "preview", episodes)

    result = {
        "source": args.video.name,
        "fps": round(fps, 3),
        "duration": round(duration, 3),
        "intro_discarded": (not args.keep_intro and bool(split_cards)),
        "episodes": episodes,
        "title_cards": [
            {
                "start": round(c.start, 3),
                "end": round(c.end, 3),
                "similarity": c.similarity,
                "abrupt_start": c.abrupt_start,
                "abrupt_end": c.abrupt_end,
                "has_title_text": c.has_title_text,
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
        # Refusing to cut without evidence is only useful if the user can
        # inspect the candidates NOW — detection takes minutes, so save the
        # preview frames here instead of making them re-run with --preview.
        save_preview(args.video, fps, cards, args.output_dir / "preview", episodes)
        print("⚠️ 集数超过 3, 很可能是误检。已跳过切割。")
        print(f"   请查看 {args.output_dir / 'preview'}/ 下的候选预览图确认。")
        print("   确认确实有多故事后，用 --episodes N 指定集数重跑切割。")
        return 1

    if args.confirm:
        preview_dir = args.output_dir / "preview"
        save_preview(args.video, fps, cards, preview_dir, episodes)
        _open_in_shell(preview_dir / "contact_sheet.jpg")
        try:
            ans = input("\n按以上方案分割? [y/N]: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() not in ("y", "yes"):
            _open_in_shell(preview_dir)
            print(f"已取消。候选预览图在 {preview_dir}/，调整后重跑即可。")
            return 2

    cut_episodes(args.video, episodes, args.output_dir, args.copy, force_cpu=args.cpu)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
