from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import download_tver as download  # noqa: E402
import split_episode as split  # noqa: E402


def sparse_file(path, size):
    with path.open("wb") as handle:
        handle.truncate(size)


@pytest.mark.parametrize("size", [0, download.MIN_FINISHED_BYTES - 1, download.MIN_FINISHED_BYTES])
def test_download_completion_threshold_ignores_partial_output(tmp_path, size):
    video = tmp_path / "941.mp4"
    sparse_file(video, size)
    (tmp_path / "941.mp4.part").write_bytes(b"not a checkpoint")
    assert download._already_downloaded(tmp_path, "941") == (video if size >= download.MIN_FINISHED_BYTES else None)


def test_completed_download_skips_before_network_or_downloader(tmp_path, monkeypatch):
    sparse_file(tmp_path / "941.mp4", download.MIN_FINISHED_BYTES)
    network = Mock(side_effect=AssertionError("must not query TVer"))
    run = Mock(side_effect=AssertionError("must not launch yt-dlp"))
    monkeypatch.setattr(download.httpx, "Client", network)
    monkeypatch.setattr(download.subprocess, "run", run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["download_tver.py", "--out-dir", str(tmp_path), "--name", "941", "--broadcast-date", "2026-11-07"],
    )
    download.main()
    network.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("force", [False, True])
def test_download_force_bypasses_completed_file_without_real_download(tmp_path, monkeypatch, force):
    sparse_file(tmp_path / "941.mp4", download.MIN_FINISHED_BYTES)
    run = Mock()
    monkeypatch.setattr(download.subprocess, "run", run)
    monkeypatch.setattr(download, "_ffmpeg_location", lambda: None)
    download.download("fake_episode", tmp_path, name="941", force=force)
    assert run.call_count == int(force)


def completed_split(tmp_path):
    video = tmp_path / "941.mp4"
    video.write_bytes(b"source is not decoded in reuse tests")
    clips = [tmp_path / f"941_{i}.mp4" for i in (1, 2)]
    for clip in clips:
        sparse_file(clip, split.MIN_CLIP_BYTES)
    manifest = tmp_path / "941_split.json"
    manifest.write_text(json.dumps({"episodes": [{"name": clip.stem} for clip in clips]}), encoding="utf-8")
    return video, clips, manifest


@pytest.mark.parametrize("incomplete", ["none", "missing_clip", "short_clip", "missing_manifest", "empty", "invalid"])
def test_split_completion_requires_manifest_and_every_completed_clip(tmp_path, incomplete):
    _, clips, manifest = completed_split(tmp_path)
    if incomplete == "missing_clip":
        clips[1].unlink()
    elif incomplete == "short_clip":
        sparse_file(clips[1], split.MIN_CLIP_BYTES - 1)
    elif incomplete == "missing_manifest":
        manifest.unlink()
    elif incomplete == "empty":
        manifest.write_text('{"episodes": []}', encoding="utf-8")
    elif incomplete == "invalid":
        manifest.write_text("not json", encoding="utf-8")
    assert split.existing_split(tmp_path, "941") == (clips if incomplete == "none" else None)


def test_completed_split_skips_detection_and_encoding(tmp_path, monkeypatch):
    video, _, _ = completed_split(tmp_path)
    detect = Mock(side_effect=AssertionError("must not decode source"))
    run = Mock(side_effect=AssertionError("must not launch ffmpeg"))
    monkeypatch.setattr(split, "detect_title_cards", detect)
    monkeypatch.setattr(split.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["split_episode.py", str(video), "-o", str(tmp_path), "--confirm-anomaly"])
    assert split.main() == 0
    detect.assert_not_called()
    run.assert_not_called()


def test_split_force_enters_mocked_detection_instead_of_reusing(tmp_path, monkeypatch):
    video, _, _ = completed_split(tmp_path)
    detect = Mock(side_effect=RuntimeError("offline detection sentinel"))
    monkeypatch.setattr(split, "detect_title_cards", detect)
    monkeypatch.setattr(sys, "argv", ["split_episode.py", str(video), "-o", str(tmp_path), "--force"])
    with pytest.raises(RuntimeError, match="offline detection sentinel"):
        split.main()
    detect.assert_called_once()


@pytest.mark.parametrize("force", [False, True])
def test_partial_split_reuses_completed_story_without_real_encoding(tmp_path, monkeypatch, force):
    video, clips, _ = completed_split(tmp_path)
    clips[1].unlink()
    episodes = [{"name": clip.stem, "start": i * 600, "end": (i + 1) * 600} for i, clip in enumerate(clips)]
    run = Mock()
    monkeypatch.setattr(split, "find_ffmpeg", lambda: "fake-ffmpeg")
    monkeypatch.setattr(split.subprocess, "run", run)
    split.cut_episodes(video, episodes, tmp_path, copy=True, force=force)
    assert run.call_count == (2 if force else 1)
    assert run.call_args.args[0][-1] == str(clips[1])
