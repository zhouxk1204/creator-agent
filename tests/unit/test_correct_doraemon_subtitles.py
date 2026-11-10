from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from creator_agent.config import TranslateSettings
from creator_agent.translate import corrector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import correct_doraemon_subtitles as cli  # noqa: E402
import translate_doraemon as synopsis  # noqa: E402


@pytest.fixture
def correction_run(tmp_path, monkeypatch):
    materials = tmp_path / "custom materials"
    materials.mkdir()
    for story in (1, 2):
        texts = ["どこでもドアだよ。", "のび太くん。", "いこうよ。", "这是任意门。", "大雄。", "走吧。"]
        blocks = [f"{i + 1}\n00:00:0{i % 3},000 --> 00:00:0{i % 3 + 1},000\n{text}" for i, text in enumerate(texts)]
        (materials / f"941_{story}_origin.srt").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        (materials / f"941_{story}_jp.md").write_text(
            f"# 日文标题{story}\n\n![封面](941_{story}.jpg)\n\nどこでもドアのお話。\n", encoding="utf-8"
        )
    settings = TranslateSettings(project_dir=str(tmp_path / "knowledge"))
    args = SimpleNamespace(target=materials, force=False)
    events = []
    prompts = []
    proc = Mock()

    def chat(received_settings, system, user, temperature=0.2):
        assert received_settings is settings
        if system == synopsis.SYSTEM_PROMPT:
            events.append("synopsis")
            return "标题：任意门\n简介：大雄用任意门去冒险。"
        if system == synopsis.TAG_SYSTEM_PROMPT:
            events.append("tags")
            return "标签：#哆啦A梦 #大雄 #任意门 #童年回忆 #新番"
        prompts.append(user)
        if system == corrector.CORRECTION_SYSTEM_PROMPT:
            events.append("correct")
            return json.dumps({"groups": [{"source_ids": [i], "text": "どこでもドアだよ。"} for i in (1, 2, 3)]})
        assert system == corrector.TRANSLATION_SYSTEM_PROMPT
        events.append("translate")
        return json.dumps({"translations": [{"id": i, "text": "这是任意门。"} for i in (1, 2, 3)]})

    def ensure_server(base_url):
        assert base_url == settings.base_url
        events.append("start")
        return proc

    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.setattr(cli, "load_settings", lambda: SimpleNamespace(translate=settings))
    monkeypatch.setattr(cli, "ensure_server", ensure_server)
    monkeypatch.setattr(cli, "stop_server", lambda owned: events.append("stop") if owned is proc else None)
    monkeypatch.setattr(synopsis, "chat", chat)
    monkeypatch.setattr(corrector, "chat", chat)
    return materials, args, events, prompts


@pytest.mark.parametrize("single_file", [False, True])
@pytest.mark.parametrize("already_running", [False, True])
def test_one_server_for_synopsis_and_subtitles(correction_run, monkeypatch, single_file, already_running):
    materials, args, events, prompts = correction_run
    if single_file:
        args.target = materials / "941_1_origin.srt"
    if already_running:
        monkeypatch.setattr(cli, "ensure_server", lambda url: events.append("reuse"))
    originals = {p: p.read_bytes() for p in materials.iterdir()}

    assert cli.main() == 0

    stories = [1] if single_file else [1, 2]
    assert events == (
        ["reuse" if already_running else "start"]
        + ["synopsis", "tags", "correct", "translate"] * len(stories)
        + ([] if already_running else ["stop"])
    )
    for prompt in prompts:
        assert "日文标题：日文标题" in prompt
        assert "中文标题：任意门" in prompt
        assert "中文简介：大雄用任意门去冒险。" in prompt
        assert "严禁从中补写台词" in prompt
        assert "标签：" not in prompt and "#童年回忆" not in prompt and "![封面]" not in prompt
    for story in stories:
        assert (materials / f"941_{story}_zh.md").is_file()
        jp = (materials / f"941_{story}_jp_fixed.srt").read_text(encoding="utf-8")
        zh = (materials / f"941_{story}_zh_fixed.srt").read_text(encoding="utf-8")
        corrector.validate_parallel_srt(jp, zh)
    for path, content in originals.items():
        assert path.read_bytes() == content
    if single_file:
        assert not (materials / "941_2_zh.md").exists()


def test_synopsis_failure_stops_only_matching_story(correction_run, monkeypatch):
    materials, _, events, _ = correction_run
    translate_note = cli.translate_note

    def fail_first(note, settings):
        if note.name == "941_1_jp.md":
            raise ValueError("synopsis failed")
        return translate_note(note, settings)

    monkeypatch.setattr(cli, "translate_note", fail_first)
    assert cli.main() == 1
    assert not (materials / "941_1_jp_fixed.srt").exists()
    assert (materials / "941_2_zh_fixed.srt").is_file()
    assert events == ["start", "synopsis", "tags", "correct", "translate", "stop"]


def test_tag_failure_still_corrects_subtitles(correction_run, monkeypatch):
    materials, _, events, _ = correction_run
    monkeypatch.setattr(synopsis, "generate_tags", Mock(side_effect=[ValueError("tags failed"), ["哆啦A梦"]]))
    assert cli.main() == 1
    assert (materials / "941_1_zh_fixed.srt").is_file()
    assert (materials / "941_2_zh_fixed.srt").is_file()
    assert "标签：" not in (materials / "941_1_zh.md").read_text(encoding="utf-8")
    assert events[-1] == "stop"


def test_existing_subtitles_protect_synopsis_until_force(correction_run):
    materials, args, events, _ = correction_run
    args.target = materials / "941_1_origin.srt"
    zh_note = materials / "941_1_zh.md"
    jp_fixed = materials / "941_1_jp_fixed.srt"
    zh_note.write_text("已有中文简介", encoding="utf-8")
    jp_fixed.write_text("已有字幕", encoding="utf-8")
    assert cli.main() == 1
    assert events == ["start", "stop"]
    assert zh_note.read_text(encoding="utf-8") == "已有中文简介"
    assert jp_fixed.read_text(encoding="utf-8") == "已有字幕"

    args.force = True
    events.clear()
    assert cli.main() == 0
    assert events == ["start", "synopsis", "tags", "correct", "translate", "stop"]
    assert "任意门" in zh_note.read_text(encoding="utf-8")


def test_subtitle_failure_keeps_generated_synopsis_and_stops_server(correction_run, monkeypatch):
    materials, args, events, _ = correction_run
    args.target = materials / "941_1_origin.srt"
    monkeypatch.setattr(corrector, "chat", Mock(side_effect=RuntimeError("model failed")))
    assert cli.main() == 1
    assert (materials / "941_1_zh.md").is_file()
    assert not (materials / "941_1_jp_fixed.srt").exists()
    assert not (materials / "941_1_zh_fixed.srt").exists()
    assert events == ["start", "synopsis", "tags", "stop"]


def test_materials_launchers_do_not_invoke_translation():
    root = Path(__file__).resolve().parents[2]
    for suffix in ("bat", "sh"):
        script = (root / "bat" / "doraemon" / f"最新话doraemon素材下载.{suffix}").read_text(encoding="utf-8")
        assert "scripts\\translate_doraemon.py" not in script
        assert "scripts/translate_doraemon.py" not in script
        assert "ensure_server" not in script and "stop_server" not in script
