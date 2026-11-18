"""Rules for the Douyin covers: naming, gating and metadata write-back (both orientations)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from creator_agent.doraemon.covers import (
    cover_field,
    cover_filename,
    cover_plan,
    cover_title,
    existing_cover,
    landscape_filename,
    portrait_filename,
    set_landscape_cover,
    set_portrait_cover,
    stories_to_render,
)
from creator_agent.doraemon.metadata import (
    MetadataError,
    load_metadata,
    normalize_metadata,
    source_hash,
    update_metadata,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def make_metadata(tmp_path, *, zh_title: str | None = "机器人服") -> Path:
    episode = "0942"
    story = {
        "story_id": "942_1",
        "story_index": 1,
        "title": "ロボット服",
        "synopsis": "あらすじ",
        "image_file": "942_1.jpg",
    }
    data = normalize_metadata(
        {
            "schema_version": 2,
            "episode": episode,
            "episode_title": "ロボット服みらいラジオ",
            "broadcast_date": "2026-11-14",
            "url": "https://www.tv-asahi.co.jp/doraemon/story/0942/",
            "stories": [story],
        }
    )
    if zh_title is not None:
        digest = source_hash(data["stories"][0])
        data["stories"][0]["zh"] = {
            "title": zh_title,
            "synopsis": "中文简介",
            "source_hash": digest,
            "translated_at": "2026-11-14T05:00:00+00:00",
        }
        data["stories"][0]["publish"]["title"] = f"哆啦a梦942 {zh_title}"
    (tmp_path / "942_1.jpg").write_bytes(b"jpg")
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_filenames_follow_story_id():
    assert portrait_filename("942_1") == "942_1_douyin_portrait.jpg"
    assert landscape_filename("942_1") == "942_1_douyin_landscape.jpg"
    assert cover_filename("942_1") == portrait_filename("942_1")
    assert cover_filename("942_1", "landscape") == landscape_filename("942_1")
    assert cover_field("portrait") == "cover_portrait_path"
    assert cover_field("landscape") == "cover_landscape_path"


def test_cover_title_requires_current_translation(tmp_path):
    path = make_metadata(tmp_path, zh_title=None)
    data = load_metadata(path)
    with pytest.raises(MetadataError, match="中文翻译缺失"):
        cover_title(data["stories"][0])


def test_cover_title_returns_chinese_title(tmp_path):
    data = load_metadata(make_metadata(tmp_path))
    assert cover_title(data["stories"][0]) == "机器人服"


def test_dy_only_skips_stories_without_a_jianying_export(tmp_path, monkeypatch, capsys):
    # 03_publish.bat only renders covers for what it can actually publish,
    # otherwise every run would pay a JianYing render for a story with no _dy
    # export yet.
    path = make_metadata(tmp_path)
    data = load_metadata(path)
    second = dict(data["stories"][0], story_id="942_2", story_index=2, image_file="942_2.jpg")
    second["zh"] = dict(data["stories"][0]["zh"], source_hash=source_hash(second))
    second["publish"] = dict(data["stories"][0]["publish"], title="哆啦a梦942 未来收音机")
    data["stories"].append(second)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "942_2.jpg").write_bytes(b"jpg")
    export = tmp_path / "942_1_dy"
    export.mkdir()
    (export / "942_1_dy.mp4").write_bytes(b"export")

    import make_douyin_covers

    monkeypatch.setattr(sys, "argv", ["make_douyin_covers.py", str(path), "--dry-run", "--dy-only"])
    assert make_douyin_covers.main() == 0
    out = capsys.readouterr().out
    assert "MAKE 942_1" in out
    assert "942_2" not in out


def test_cover_step_accepts_forwarded_publish_arguments(tmp_path, monkeypatch, capsys):
    import make_douyin_covers

    path = make_metadata(tmp_path)
    export = tmp_path / "942_1_dy"
    export.mkdir()
    (export / "942_1_dy.mp4").write_bytes(b"export")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_douyin_covers.py",
            str(path),
            "--dy-only",
            "--story",
            "942_1",
            "--dry-run",
            "--yes",
            "--headed",
            "--only",
            "douyin",
            "--schedule",
            "2026-12-01 10:15",
            "--tid",
            "127",
            "--douyin-account",
            "my-douyin",
            "--bilibili-account",
            "my-bilibili",
        ],
    )

    assert make_douyin_covers.main() == 0
    assert "MAKE 942_1" in capsys.readouterr().out
    assert not (tmp_path / portrait_filename("942_1")).exists()
    assert not (tmp_path / landscape_filename("942_1")).exists()


@pytest.mark.parametrize("flag", ["--ask", "--prompt-target"])
def test_cover_step_passes_prompt_option_to_target_picker(tmp_path, monkeypatch, flag):
    import make_douyin_covers

    path = make_metadata(tmp_path)
    calls = []

    def choose_target(value, ask=False):
        calls.append((value, ask))
        return path

    monkeypatch.setattr(make_douyin_covers, "choose_target", choose_target)
    monkeypatch.setattr(sys, "argv", ["make_douyin_covers.py", flag, "--dry-run"])

    assert make_douyin_covers.main() == 0
    assert calls == [(None, True)]


def test_cover_step_handles_cancelled_target_prompt(monkeypatch, capsys):
    import make_douyin_covers

    def choose_target(value, ask=False):
        raise EOFError("cancelled")

    monkeypatch.setattr(make_douyin_covers, "choose_target", choose_target)
    monkeypatch.setattr(sys, "argv", ["make_douyin_covers.py", "--ask"])

    assert make_douyin_covers.main() == 2
    assert "cancelled" in capsys.readouterr().err


def test_set_portrait_cover_writes_relative_name(tmp_path):
    path = make_metadata(tmp_path)
    cover = tmp_path / portrait_filename("942_1")
    cover.write_bytes(b"jpg")
    data = set_portrait_cover(path, "942_1", cover)
    assert data["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == "942_1_douyin_portrait.jpg"
    assert load_metadata(path)["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == portrait_filename("942_1")


def test_set_portrait_cover_keeps_handmade_cover_without_force(tmp_path):
    path = make_metadata(tmp_path)
    handmade = tmp_path / "custom.jpg"
    handmade.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", handmade)
    generated = tmp_path / portrait_filename("942_1")
    generated.write_bytes(b"jpg")
    with pytest.raises(MetadataError, match="已有自定义"):
        set_portrait_cover(path, "942_1", generated)
    assert load_metadata(path)["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == "custom.jpg"


def test_set_portrait_cover_force_replaces(tmp_path):
    path = make_metadata(tmp_path)
    handmade = tmp_path / "custom.jpg"
    handmade.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", handmade)
    generated = tmp_path / portrait_filename("942_1")
    generated.write_bytes(b"jpg")
    data = set_portrait_cover(path, "942_1", generated, overwrite=True)
    assert data["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == portrait_filename("942_1")


def test_cover_plan_skips_when_cover_exists(tmp_path):
    path = make_metadata(tmp_path)
    cover = tmp_path / portrait_filename("942_1")
    cover.write_bytes(b"jpg")
    update_metadata(
        path,
        lambda data: data["stories"][0]["publish"]["douyin"].update(
            {"cover_portrait_path": portrait_filename("942_1")}
        ),
    )
    data = load_metadata(path)
    assert existing_cover(path, data["stories"][0]) == cover
    assert cover_plan(path, data["stories"][0], force=False) is None
    target, title = cover_plan(path, data["stories"][0], force=True)
    assert target == cover
    assert title == "机器人服"


def test_stories_to_render_selects_requested(tmp_path):
    data = load_metadata(make_metadata(tmp_path))
    assert [story["story_id"] for story in stories_to_render(data, None)] == ["942_1"]
    assert [story["story_id"] for story in stories_to_render(data, ["942_1"])] == ["942_1"]
    with pytest.raises(MetadataError):
        stories_to_render(data, ["999_1"])


def test_set_landscape_cover_writes_its_own_field(tmp_path):
    path = make_metadata(tmp_path)
    portrait = tmp_path / portrait_filename("942_1")
    portrait.write_bytes(b"jpg")
    landscape = tmp_path / landscape_filename("942_1")
    landscape.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", portrait)
    data = set_landscape_cover(path, "942_1", landscape)
    douyin = data["stories"][0]["publish"]["douyin"]
    assert douyin["cover_portrait_path"] == portrait_filename("942_1")
    assert douyin["cover_landscape_path"] == landscape_filename("942_1")


def test_cover_plan_is_per_orientation(tmp_path):
    path = make_metadata(tmp_path)
    portrait = tmp_path / portrait_filename("942_1")
    portrait.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", portrait)
    data = load_metadata(path)
    story = data["stories"][0]
    # Portrait already exists, landscape still missing.
    assert cover_plan(path, story, force=False, orientation="portrait") is None
    plan = cover_plan(path, story, force=False, orientation="landscape")
    assert plan is not None
    assert plan[0] == tmp_path / landscape_filename("942_1")
    assert plan[1] == "机器人服"
