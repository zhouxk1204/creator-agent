"""One-shot: download a specific Douyin video and transcribe it.

For a single video by URL, no DB / no pipeline. The video is downloaded to
``storage/_adhoc/{video_id}/video.mp4`` and audio to ``audio.wav``, then
FunASR is run via the creator-asr conda env, and the transcript is written to
``transcript.txt`` plus ``transcript.json`` (same schema the per-creator
pipeline writes, including the timestamped segments).

Usage:
    uv run python scripts/transcribe_one.py [douyin_video_url_or_id]

The argument may be a ``/video/<id>`` URL, a profile/modal URL carrying
``modal_id=<id>``, or a bare numeric id. With no argument the built-in default
video is used.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from creator_agent.asr.audio_extractor import extract_audio
from creator_agent.asr.worker import _RESULT_BEGIN, _RESULT_END  # type: ignore
from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.collector.douyin.meta import fetch_video_meta
from creator_agent.config import load_settings
from creator_agent.downloader.downloader import Downloader
from creator_agent.models.transcript import Transcript, TranscriptSegment

DEFAULT_VIDEO_ID = "7677189247543135514"


def parse_target(raw: str | None) -> tuple[str, str]:
    """Return ``(video_id, page_url)`` for a Douyin URL, modal URL, or bare id."""
    if not raw:
        return DEFAULT_VIDEO_ID, f"https://www.douyin.com/video/{DEFAULT_VIDEO_ID}"
    raw = raw.strip()
    if raw.isdigit():
        return raw, f"https://www.douyin.com/video/{raw}"
    match = re.search(r"/video/(\d+)", raw) or re.search(r"modal_id=(\d+)", raw) or re.search(r"(\d{10,})", raw)
    if not match:
        raise SystemExit(f"Could not find a Douyin video id in: {raw!r}")
    video_id = match.group(1)
    return video_id, f"https://www.douyin.com/video/{video_id}"


def main(target: str | None = None) -> int:
    video_id, video_url = parse_target(target)
    out_dir = Path("storage/_adhoc") / video_id
    settings = load_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / "video.mp4"
    audio_path = out_dir / "audio.wav"
    txt_path = out_dir / "transcript.txt"

    # 1) Resolve CDN URL via Playwright, then download with httpx (cookies+Referer+UA).
    browser = BrowserManager(
        BrowserConfig(user_data_dir=settings.browser.user_data_dir, headless=True)
    )
    browser.start()
    try:
        print(f"[1/3] Resolving CDN URL via browser for: {video_url}")
        meta = fetch_video_meta(video_url, browser, settings.downloader.timeout_sec)
        print(f"      title     : {meta.title[:80]!r}")
        print(f"      published : {meta.published_at}")
        print(f"      likes     : {meta.likes}  comments: {meta.comments}  duration: {meta.duration_sec}s")
        print(f"      cdn_url   : {'resolved' if meta.cdn_url else 'NOT resolved'}")
        if not meta.cdn_url:
            print("ERROR: could not resolve CDN URL.")
            return 1

        downloader = Downloader(
            storage=None,  # type: ignore[arg-type]
            browser=browser,
            timeout_sec=settings.downloader.timeout_sec,
            retries=settings.downloader.retry,
        )
        # We bypass the storage layer; download straight to out_dir.
        from creator_agent.collector.douyin.meta import DOUYIN_REFERER, USER_AGENT
        import httpx
        import time as _t

        cookies = browser.get_cookies("www.douyin.com")
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        headers = {"Referer": DOUYIN_REFERER, "User-Agent": USER_AGENT}

        last_exc: Exception | None = None
        for attempt in range(settings.downloader.retry):
            try:
                with httpx.Client(
                    timeout=settings.downloader.timeout_sec,
                    follow_redirects=True,
                    cookies=cookie_dict,
                    headers=headers,
                ) as client:
                    resp = client.get(meta.cdn_url)
                    resp.raise_for_status()
                    video_path.write_bytes(resp.content)
                break
            except Exception as e:
                last_exc = e
                print(f"      download attempt {attempt+1} failed: {e}")
                if attempt < settings.downloader.retry - 1:
                    _t.sleep(2 ** attempt)
        else:
            print(f"ERROR: download failed after retries: {last_exc}")
            return 1

        size = video_path.stat().st_size
        head = video_path.read_bytes()[:8]
        print(f"[2/3] Video saved: {video_path}  ({size:,} bytes, head={head.hex()})")
    finally:
        browser.close()

    # 2) Extract audio
    print(f"[3/3] Extracting audio -> {audio_path}")
    extract_audio(video_path, audio_path, settings.asr.ffmpeg_path or None)
    print(f"      audio size : {audio_path.stat().st_size:,} bytes")

    # 3) Run ASR worker
    env_python = settings.asr.env_python
    if not env_python or not Path(env_python).exists():
        print(f"ERROR: creator-asr python not found: {env_python!r}")
        return 2
    from creator_agent.asr import worker as _worker
    worker_script = settings.asr.worker_script or str(Path(_worker.__file__))

    jobs = [{"video_id": video_id, "audio_path": str(audio_path)}]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False)
        jobs_path = f.name

    cmd = [
        env_python,
        worker_script,
        "--jobs", jobs_path,
        "--model", settings.asr.model,
        "--vad-model", settings.asr.vad_model,
        "--punc-model", settings.asr.punc_model,
        "--device", settings.asr.device,
    ]
    print(f"      running : {cmd[0]} {Path(worker_script).name} --jobs ... --device {settings.asr.device}")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, capture_output=True, env=env)
    Path(jobs_path).unlink(missing_ok=True)
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        print("ERROR: ASR worker failed.")
        print("stdout tail:\n", stdout[-1500:])
        print("stderr tail:\n", (proc.stderr or b"").decode("utf-8", errors="replace")[-1500:])
        return 3

    begin = stdout.rfind(_RESULT_BEGIN)
    end = stdout.rfind(_RESULT_END)
    if begin < 0 or end < 0 or end <= begin:
        print("ERROR: ASR worker output missing result markers.")
        print("stdout tail:\n", stdout[-800:])
        return 4
    payload = json.loads(stdout[begin + len(_RESULT_BEGIN):end].strip())
    if not payload or not payload[0].get("ok"):
        print("ERROR: ASR failed:", payload[0].get("error") if payload else "no result")
        return 5

    text = payload[0].get("text", "")
    segs = payload[0].get("segments", [])
    duration = payload[0].get("duration_sec")
    txt_path.write_text(text, encoding="utf-8")

    transcript = Transcript(
        video_id=video_id,
        text=text,
        segments=[TranscriptSegment(**s) for s in segs],
        language="zh",
        model=settings.asr.model,
        duration_sec=duration,
        created_at=datetime.now(UTC),
    )
    json_path = out_dir / "transcript.json"
    json_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    print(f"\nDONE. transcript written to: {txt_path}")
    print(f"       timestamped segments: {json_path}")
    print(f"  duration  : {duration}s")
    print(f"  segments  : {len(segs)}")
    print(f"  text len  : {len(text)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
