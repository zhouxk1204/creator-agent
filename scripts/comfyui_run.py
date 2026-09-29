"""Run a ComfyUI workflow from the terminal with real-time progress.

Why this exists: the Anime 2x RealESRGAN workflow uses a VHS meta batch
(VHS_BatchManager), which re-queues the workflow once per batch of frames.
The ComfyUI web UI progress bar resets on every batch, so for a long video
you never see overall progress. This script submits the workflow over the
HTTP API and listens on the websocket for executing/progress events,
rendering one live status line: batch x/N, current node, elapsed, ETA.

It accepts the GUI-format workflow JSON (the kind ComfyUI saves by default)
and converts it to API format on the fly, so the repo file can stay the one
you edit in the UI. Total batch count is estimated from the input video's
frame count (ffprobe if available, otherwise a built-in mp4 parser), divided
by frames_per_batch; if neither works it shows "batch x/?".

Usage:
    uv run python scripts/comfyui_run.py                     # submit + watch
    uv run python scripts/comfyui_run.py --attach            # watch a job queued in the web UI
    uv run python scripts/comfyui_run.py --video other.mp4   # override the input video
    uv run python scripts/comfyui_run.py --prefix run2       # override output filename_prefix
    uv run python scripts/comfyui_run.py --frames-per-batch 16
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import websocket  # websocket-client

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW = REPO_ROOT / "ComfyUI_Anime_2x_RealESRGAN_MetaBatch_TotalProgress.json"

# How long the queue must stay empty (after activity) before we call it done.
# Between meta batches the gap is ~instant, so a few seconds is a safe margin.
QUIET_SECONDS = 5.0

# Make non-ASCII print correctly on the Windows console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GUI workflow JSON -> API format
# ---------------------------------------------------------------------------


def gui_to_api(wf: dict) -> dict:
    """Convert a UI-saved workflow (nodes/links) to the /prompt API format.

    Widget values come from widgets_values_named, filtered to the widget names
    declared in each node's inputs, so UI-only extras (videopreview, the hidden
    upload widget) are dropped.
    """
    link_by_id = {link[0]: link for link in wf.get("links", [])}
    api = {}
    for node in wf["nodes"]:
        if node.get("mode", 0) in (2, 4):  # muted / bypassed
            continue
        inputs = {}
        for inp in node.get("inputs") or []:
            link_id = inp.get("link")
            if link_id is None or link_id not in link_by_id:
                continue
            link = link_by_id[link_id]  # [id, src_node, src_slot, dst_node, dst_slot, type]
            inputs[inp["name"]] = [str(link[1]), link[2]]
        widget_names = [i["widget"]["name"] for i in node.get("inputs") or [] if i.get("widget")]
        named = node.get("widgets_values_named")
        if isinstance(named, dict):
            for name in widget_names:
                if name in named and named[name] is not None:
                    inputs[name] = named[name]
        else:  # positional fallback
            for name, value in zip(widget_names, node.get("widgets_values") or []):
                inputs[name] = value
        api[str(node["id"])] = {"class_type": node["type"], "inputs": inputs}
    return api


def load_api_workflow(path: Path) -> tuple[dict, dict | None]:
    """Return (api_prompt, gui_workflow_or_None)."""
    wf = json.loads(path.read_text(encoding="utf-8"))
    if "nodes" in wf and "links" in wf:
        return gui_to_api(wf), wf
    return wf, None


def find_node(api: dict, class_type: str) -> str | None:
    for node_id, node in api.items():
        if node.get("class_type") == class_type:
            return node_id
    return None


# ---------------------------------------------------------------------------
# Total-batch estimate from the input video
# ---------------------------------------------------------------------------


def probe_frames(video_path: Path) -> int | None:
    """Frame count of the input video; None if it can't be determined.

    Tries ffprobe first, then falls back to a small built-in mp4 parser
    (sums the video track's stts sample counts) so no ffmpeg install is needed.
    """
    if not video_path.is_file():
        return None
    return _probe_frames_ffprobe(video_path) or _probe_frames_mp4(video_path)


def _probe_frames_ffprobe(video_path: Path) -> int | None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_frames,r_frame_rate,duration",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stream = json.loads(out.stdout)["streams"][0]
        if stream.get("nb_frames", "N/A") not in ("N/A", None):
            return int(stream["nb_frames"])
        num, den = stream["r_frame_rate"].split("/")
        return int(float(stream["duration"]) * int(num) / int(den))
    except Exception:
        return None


def _mp4_boxes(f, end: int):
    """Yield (type, body_start, box_end) for each top-level box up to `end`."""
    while f.tell() < end:
        pos = f.tell()
        header = f.read(8)
        if len(header) < 8:
            return
        size, typ = struct.unpack(">I4s", header)
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        else:
            hdr = 8
            if size == 0:
                size = end - pos
        if size < hdr or pos + size > end:
            return
        yield typ, pos + hdr, pos + size
        f.seek(pos + size)


def _trak_frames_mp4(f, start: int, end: int) -> int | None:
    """Frame count of one trak, or None unless it is the video track."""
    handler = None
    frames = None
    f.seek(start)
    for _typ, mdia_body, mdia_end in _mp4_boxes(f, end):  # mdia
        f.seek(mdia_body)
        for typ2, body2, end2 in _mp4_boxes(f, mdia_end):
            if typ2 == b"hdlr":
                f.seek(body2 + 8)  # version/flags + pre_defined
                handler = f.read(4)
            elif typ2 == b"minf":
                f.seek(body2)
                for typ3, body3, end3 in _mp4_boxes(f, end2):
                    if typ3 != b"stbl":
                        continue
                    f.seek(body3)
                    for typ4, body4, _end4 in _mp4_boxes(f, end3):
                        if typ4 == b"stts":
                            f.seek(body4 + 4)  # version/flags
                            (entries,) = struct.unpack(">I", f.read(4))
                            total = 0
                            for _ in range(entries):
                                total += struct.unpack(">I", f.read(4))[0]
                                f.read(4)  # sample_delta, unused
                            frames = total
    return frames if handler == b"vide" else None


def _probe_frames_mp4(video_path: Path) -> int | None:
    if video_path.suffix.lower() not in (".mp4", ".mov", ".m4v"):
        return None
    try:
        with open(video_path, "rb") as f:
            file_end = os.fstat(f.fileno()).st_size
            for typ, body, box_end in _mp4_boxes(f, file_end):
                if typ != b"moov":
                    continue
                f.seek(body)
                for typ2, body2, end2 in _mp4_boxes(f, box_end):
                    if typ2 == b"trak":
                        frames = _trak_frames_mp4(f, body2, end2)
                        if frames:
                            return frames
    except Exception:
        return None
    return None


def estimate_total_batches(api: dict, gui: dict | None, comfy_dir: Path | None) -> int | None:
    load_id = find_node(api, "VHS_LoadVideo")
    batch_id = find_node(api, "VHS_BatchManager")
    if not load_id or not batch_id:
        return None
    li = api[load_id]["inputs"]
    frames_per_batch = max(1, int(api[batch_id]["inputs"].get("frames_per_batch", 1)))
    if comfy_dir is None:
        return None
    frames = probe_frames(comfy_dir / "input" / str(li.get("video", "")))
    if not frames:
        return None
    frames = max(0, frames - int(li.get("skip_first_frames", 0)))
    frames = math.ceil(frames / max(1, int(li.get("select_every_nth", 1))))
    cap = int(li.get("frame_load_cap", 0))
    if cap:
        frames = min(frames, cap)
    return math.ceil(frames / frames_per_batch)


def derive_comfy_dir(gui: dict | None) -> Path | None:
    """ComfyUI root from the output fullpath stashed in a videopreview widget."""
    if not gui:
        return None
    for node in gui.get("nodes", []):
        named = node.get("widgets_values_named") or {}
        params = (named.get("videopreview") or {}).get("params") or {}
        fullpath = params.get("fullpath")
        if fullpath and "\\output\\" in fullpath:
            return Path(fullpath.split("\\output\\")[0])
    return None


# ---------------------------------------------------------------------------
# Live status line
# ---------------------------------------------------------------------------

_start = time.monotonic()
_last_line = 0


def fmt_secs(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def status(line: str) -> None:
    global _last_line
    width = shutil.get_terminal_size((100, 20)).columns
    line = line[: width - 1]
    pad = max(0, _last_line - len(line))
    sys.stdout.write("\r" + line + " " * pad)
    sys.stdout.flush()
    _last_line = len(line)


def println(text: str = "") -> None:
    sys.stdout.write("\r" + " " * _last_line + "\r" + text + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Submit + monitor
# ---------------------------------------------------------------------------


def queue_empty(client: httpx.Client, server: str) -> bool:
    try:
        q = client.get(f"http://{server}/queue").json()
        return not q.get("queue_running") and not q.get("queue_pending")
    except Exception:
        return False


def monitor(server: str, client_id: str, load_node: str | None, total_batches: int | None) -> int:
    """Listen on the websocket until the queue drains. Returns exit code."""
    global _start
    _start = time.monotonic()
    ws = websocket.create_connection(f"ws://{server}/ws?clientId={client_id}", timeout=2)
    ws.settimeout(2)

    batch = 0
    cur_node = ""
    prog: tuple[int, int] | None = None  # (value, max) intra-node progress
    outputs: list[str] = []
    saw_activity = False
    quiet_since: float | None = None
    exit_code = 0

    status("waiting for ComfyUI to start the job ...")
    try:
        while True:
            try:
                msg = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                if saw_activity:
                    with httpx.Client(timeout=10) as c:
                        if queue_empty(c, server):
                            quiet_since = quiet_since or time.monotonic()
                            if time.monotonic() - quiet_since >= QUIET_SECONDS:
                                break
                        else:
                            quiet_since = None
                continue

            mtype, data = msg.get("type"), msg.get("data") or {}

            if mtype == "executing":
                node = data.get("node")
                if node is None:
                    continue  # one prompt finished; meta batch re-queues, so don't stop here
                saw_activity = True
                quiet_since = None
                if load_node and node == load_node:
                    batch += 1
                    prog = None
                cur_node = node
            elif mtype == "progress":
                saw_activity = True
                quiet_since = None
                value, maxv = data.get("value", 0), data.get("max", 0)
                prog = (value, maxv) if maxv else None
            elif mtype == "executed":
                for gif in (data.get("output") or {}).get("gifs") or []:
                    name = str(Path(gif.get("subfolder", "")) / gif["filename"])
                    if name not in outputs:
                        outputs.append(name)
            elif mtype == "execution_error":
                println()
                println(
                    f"[X] execution error in node {data.get('node_id')} "
                    f"({data.get('node_type')}): {data.get('exception_message')}"
                )
                exit_code = 1
                break
            elif mtype == "execution_interrupted":
                println()
                println("[!] execution interrupted")
                exit_code = 1
                break
            else:
                continue

            # Render the live line.
            elapsed = fmt_secs(time.monotonic() - _start)
            total = str(total_batches) if total_batches else "?"
            parts = [f"batch {batch}/{total}"]
            if total_batches and batch:
                done = max(0, batch - 1)  # current batch still running
                rate = (time.monotonic() - _start) / max(1, done)
                parts.append(f"{done / total_batches:>6.1%}")
                parts.append(f"ETA {fmt_secs(rate * (total_batches - done))}")
            parts.append(f"node {cur_node or '-'}")
            if prog and prog[1] > 1:
                parts.append(f"{prog[0]}/{prog[1]} ({prog[0] / prog[1]:.0%})")
            parts.append(f"elapsed {elapsed}")
            status(" | ".join(parts))
    except KeyboardInterrupt:
        println()
        println("[!] stopped watching -- the job keeps running inside ComfyUI")
        return exit_code
    finally:
        ws.close()

    println()
    if exit_code == 0:
        println(f"[OK] done in {fmt_secs(time.monotonic() - _start)} ({batch} batches)")
        for name in outputs:
            println(f"     output: {name}")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "workflow", nargs="?", type=Path, default=DEFAULT_WORKFLOW, help="workflow JSON (GUI or API format)"
    )
    parser.add_argument("--server", default="127.0.0.1:8188", help="ComfyUI host:port")
    parser.add_argument("--attach", action="store_true", help="don't submit; just watch a job queued elsewhere")
    parser.add_argument("--video", help="override the VHS_LoadVideo input filename")
    parser.add_argument("--prefix", help="override the VHS_VideoCombine filename_prefix")
    parser.add_argument("--frames-per-batch", type=int, help="override VHS_BatchManager frames_per_batch")
    parser.add_argument(
        "--comfy-dir", type=Path, help="ComfyUI root (for frame-count estimate); default: derived from the workflow"
    )
    args = parser.parse_args()

    api, gui = load_api_workflow(args.workflow)

    # Apply overrides.
    if args.video and (load_id := find_node(api, "VHS_LoadVideo")):
        api[load_id]["inputs"]["video"] = args.video
    if args.prefix and (comb_id := find_node(api, "VHS_VideoCombine")):
        api[comb_id]["inputs"]["filename_prefix"] = args.prefix
    if args.frames_per_batch and (batch_id := find_node(api, "VHS_BatchManager")):
        api[batch_id]["inputs"]["frames_per_batch"] = args.frames_per_batch

    comfy_dir = args.comfy_dir or derive_comfy_dir(gui)
    total_batches = estimate_total_batches(api, gui, comfy_dir)
    load_node = find_node(api, "VHS_LoadVideo")

    client_id = uuid.uuid4().hex
    if not args.attach:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(f"http://{args.server}/prompt", json={"prompt": api, "client_id": client_id})
        except httpx.ConnectError:
            println(f"[X] ComfyUI not reachable at {args.server} -- start it first.")
            return 1
        if resp.status_code != 200:
            println(f"[X] ComfyUI rejected the workflow ({resp.status_code}):")
            println(json.dumps(resp.json(), indent=2, ensure_ascii=False)[:4000])
            return 1
        prompt_id = resp.json().get("prompt_id", "?")
        total = f"~{total_batches} batches" if total_batches else "batch count unknown (ffprobe/input video not found)"
        println(f"[*] submitted prompt {prompt_id} -- {total}")
    else:
        println("[*] attach mode -- watching ComfyUI for activity")

    return monitor(args.server, client_id, load_node, total_batches)


if __name__ == "__main__":
    sys.exit(main())
