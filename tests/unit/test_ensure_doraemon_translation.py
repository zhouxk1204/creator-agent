from __future__ import annotations

import json
import sys

from creator_agent.doraemon.metadata import normalize_metadata, source_hash
from scripts import ensure_doraemon_translation as ensure


def write_metadata(folder, stories, episode="943"):
    data = normalize_metadata({"episode": episode, "stories": stories})
    (folder / "metadata.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def run(monkeypatch, folder, *extra):
    monkeypatch.setattr(sys, "argv", ["ensure_doraemon_translation.py", str(folder), *extra])
    return ensure.main()


def translated_story(data, index=1, tags=None):
    """Fill zh + platform tags so the story is publishable."""
    story = data["stories"][index - 1]
    story["zh"] = {
        "title": "招引镜",
        "synopsis": "大雄他们用招引镜抓九官鸟。",
        "source_hash": source_hash(story),
    }
    story["publish"]["title"] = "哆啦a梦943 招引镜"
    tags = tags or ["哆啦", "蓝胖子", "童年回忆", "童年回忆杀", "招引镜抓鸟"]
    story["publish"]["douyin"]["tags"] = list(tags[:5])
    story["publish"]["bilibili"]["tags"] = list(tags)
    return story


def test_check_flags_a_b_video_that_has_no_translation(tmp_path, monkeypatch, capsys):
    write_metadata(tmp_path, [{"title": "引きよせかがみ", "synopsis": "あらすじ。"}])
    (tmp_path / "943_1_b.mp4").write_bytes(b"video")

    assert run(monkeypatch, tmp_path, "--b", "--check") == ensure.MISSING_RC

    out = capsys.readouterr().out
    assert "943_1" in out
    assert "中文简介" in out


def test_check_ignores_stories_with_no_media(tmp_path, monkeypatch, capsys):
    data = write_metadata(
        tmp_path,
        [
            {"title": "引きよせかがみ", "synopsis": "あらすじ。"},
            {"title": "ダジャレーランド", "synopsis": "あらすじ。"},
        ],
    )
    translated_story(data, 1)
    (tmp_path / "metadata.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "943_1_dy").mkdir()
    (tmp_path / "943_1_dy" / "943_1_dy.mp4").write_bytes(b"video")

    # 943_2 has no 剪映 export yet, so publishing 943_1 must not wait for it.
    assert run(monkeypatch, tmp_path, "--dy", "--check") == ensure.READY_RC
    assert "943_2" not in capsys.readouterr().out


def test_check_passes_when_synopsis_and_tags_are_present(tmp_path, monkeypatch):
    data = write_metadata(tmp_path, [{"title": "引きよせかがみ", "synopsis": "あらすじ。"}])
    translated_story(data, 1)
    (tmp_path / "metadata.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "943_1_dy").mkdir()
    (tmp_path / "943_1_dy" / "943_1_dy.mp4").write_bytes(b"video")

    assert run(monkeypatch, tmp_path, "--dy", "--check") == ensure.READY_RC


def test_flat_root_export_counts_as_publishable(tmp_path, monkeypatch):
    write_metadata(tmp_path, [{"title": "引きよせかがみ", "synopsis": "あらすじ。"}])
    (tmp_path / "943_1_dy.mp4").write_bytes(b"video")

    assert run(monkeypatch, tmp_path, "--dy", "--check") == ensure.MISSING_RC


def test_missing_tags_only_needs_tag_generation(tmp_path, monkeypatch, capsys):
    data = write_metadata(tmp_path, [{"title": "引きよせかがみ", "synopsis": "あらすじ。"}])
    story = translated_story(data, 1)
    story["publish"]["douyin"]["tags"] = []
    (tmp_path / "metadata.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "943_1_dy").mkdir()
    (tmp_path / "943_1_dy" / "943_1_dy.mp4").write_bytes(b"video")

    assert run(monkeypatch, tmp_path, "--dy", "--check") == ensure.MISSING_RC
    out = capsys.readouterr().out
    assert "douyin标签" in out
    assert "中文简介" not in out


def test_dry_run_reports_without_calling_the_model(tmp_path, monkeypatch):
    write_metadata(tmp_path, [{"title": "引きよせかがみ", "synopsis": "あらすじ。"}])
    (tmp_path / "943_1_b.mp4").write_bytes(b"video")

    def boom(*args, **kwargs):
        raise AssertionError("dry-run must not translate")

    monkeypatch.setattr(ensure, "translate_metadata", boom)

    # 03_publish.bat calls this with "%* --check": dry-run must still win, or a
    # dry run would start the LLM and rewrite metadata.
    assert run(monkeypatch, tmp_path, "--b", "--dry-run", "--check") == ensure.READY_RC


def test_no_batch_flag_uses_the_media_actually_on_disk(tmp_path, monkeypatch):
    # The launcher's prepare step runs before the platform is chosen, so it
    # passes neither --b nor --dy.
    write_metadata(tmp_path, [{"title": "引きよせかがみ", "synopsis": "あらすじ。"}])
    (tmp_path / "943_1_b.mp4").write_bytes(b"video")

    assert run(monkeypatch, tmp_path, "--check") == ensure.MISSING_RC


def test_translation_mode_fills_in_and_stops_on_llm_failure(tmp_path, monkeypatch):
    write_metadata(tmp_path, [{"title": "引きよせかがみ", "synopsis": "あらすじ。"}])
    (tmp_path / "943_1_b.mp4").write_bytes(b"video")

    def fail(*args, **kwargs):
        raise ValueError("model replied in Japanese")

    monkeypatch.setattr(ensure, "translate_metadata", fail)

    assert run(monkeypatch, tmp_path, "--b") == ensure.FAILED_RC
