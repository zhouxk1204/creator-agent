from __future__ import annotations

import json
import sys

from PIL import Image

from creator_agent.doraemon.metadata import normalize_metadata
from scripts import make_bilibili_covers


def test_bilibili_cover_is_real_jpeg_at_the_same_4k_dimensions(tmp_path):
    source = tmp_path / "942_1_4k.jpg"
    Image.new("RGB", (3840, 2160), (35, 120, 210)).save(source, format="PNG")
    original = source.read_bytes()

    target = make_bilibili_covers.make_bilibili_cover(source)

    assert target.name == "942_1_4k_bilibili.jpg"
    assert source.read_bytes() == original
    assert target.stat().st_size <= make_bilibili_covers.MAX_COVER_BYTES
    with Image.open(target) as cover:
        assert cover.format == "JPEG"
        assert cover.size == (3840, 2160)


def test_cover_step_accepts_forwarded_publish_arguments(tmp_path, monkeypatch):
    metadata = normalize_metadata({"episode": "942", "stories": [{"title": "物語", "synopsis": "あらすじ。"}]})
    (tmp_path / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "942_1_b.mp4").write_bytes(b"video")
    Image.new("RGB", (3840, 2160), (35, 120, 210)).save(tmp_path / "942_1_4k.jpg", format="PNG")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_bilibili_covers.py",
            str(tmp_path),
            "--story",
            "942_1",
            "--dry-run",
            "--schedule",
            "2026-12-01 10:15",
            "--only",
            "bilibili",
        ],
    )

    assert make_bilibili_covers.main() == 0
    assert (tmp_path / "942_1_4k_bilibili.jpg").is_file()
