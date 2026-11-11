from __future__ import annotations

import json
import re
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
def materials_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    return logs


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_default_target_prefers_saved_materials(materials_logs, tmp_path, encoding):
    materials = tmp_path / "自定义 materials !" / "doraemon_941"
    (materials_logs / ".last_doraemon_materials").write_text(f"{materials}\r\n", encoding=encoding)
    (materials_logs / ".last_fetched").write_text("999", encoding="utf-8")

    assert cli.default_target() == materials


@pytest.mark.parametrize("content", [b"", b" \r\n", b"\xff"])
def test_invalid_materials_record_is_not_silently_replaced(materials_logs, content):
    (materials_logs / ".last_doraemon_materials").write_bytes(content)
    (materials_logs / ".last_fetched").write_text("941", encoding="utf-8")

    with pytest.raises(corrector.SubtitleCorrectionError, match=r"logs/\.last_doraemon_materials"):
        cli.default_target()


def test_default_target_falls_back_to_legacy_episode(materials_logs, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (materials_logs / ".last_fetched").write_text("941\n", encoding="utf-8")

    assert cli.default_target() == tmp_path / "Desktop" / "doraemon_941"


def test_default_target_requires_saved_path_or_episode(materials_logs):
    with pytest.raises(corrector.SubtitleCorrectionError, match="均不存在"):
        cli.default_target()


def test_prompt_target_uses_displayed_snapshot(materials_logs, tmp_path, monkeypatch, capsys):
    state = materials_logs / ".last_doraemon_materials"
    displayed = tmp_path / "first materials"
    replacement = tmp_path / "replacement materials"
    state.write_text(f"{displayed}\n", encoding="utf-8-sig")

    def mutate_state(_prompt):
        state.write_text(f"{replacement}\n", encoding="utf-8")
        return ""

    monkeypatch.setattr("builtins.input", mutate_state)

    assert cli.prompt_target() == displayed
    assert str(displayed) in capsys.readouterr().out


def test_prompt_target_accepts_copy_as_path_override(materials_logs, tmp_path, monkeypatch):
    override = tmp_path / "pasted materials"
    monkeypatch.setattr("builtins.input", lambda _prompt: f'  "{override}"  ')

    assert cli.prompt_target() == override


def test_prompt_target_requires_path_when_no_default(materials_logs, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    with pytest.raises(corrector.SubtitleCorrectionError, match="均不存在"):
        cli.prompt_target()


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
    args = SimpleNamespace(target=materials, force=False, prompt_target=False)
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
            return "\n".join(f"{i}|どこでもドアだよ。" for i in (1, 2, 3))
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


def test_no_target_processes_saved_materials(correction_run, materials_logs):
    materials, args, events, _ = correction_run
    args.target = None
    (materials_logs / ".last_doraemon_materials").write_text(f"{materials}\n", encoding="utf-8")
    (materials_logs / ".last_fetched").write_text("999", encoding="utf-8")

    assert cli.main() == 0
    assert (materials / "941_1_zh_fixed.srt").is_file()
    assert (materials / "941_2_zh_fixed.srt").is_file()
    assert events[0] == "start" and events[-1] == "stop"


def test_explicit_target_overrides_saved_materials(correction_run, materials_logs, monkeypatch):
    materials, _, _, _ = correction_run
    state = materials_logs / ".last_doraemon_materials"
    state.write_text("nonexistent saved folder\n", encoding="utf-8")
    default_target = Mock(side_effect=AssertionError("explicit target must not consult saved state"))
    monkeypatch.setattr(cli, "default_target", default_target)

    assert cli.main() == 0
    default_target.assert_not_called()
    assert (materials / "941_1_zh_fixed.srt").is_file()
    assert state.read_text(encoding="utf-8") == "nonexistent saved folder\n"


def test_materials_launchers_record_folder_before_download():
    root = Path(__file__).resolve().parents[2]
    for suffix, separator in (("bat", "\\"), ("sh", "/")):
        launcher = root / "bat" / "doraemon" / f"最新话doraemon素材下载.{suffix}"
        data = launcher.read_bytes()
        if suffix == "bat":
            assert b"\n" not in data.replace(b"\r\n", b"")
        script = data.decode("utf-8").replace("\r\n", "\n")
        marker = script.index(".last_doraemon_materials")
        assert script.index("mkdir") < marker < script.index(f"scripts{separator}download_tver.py")
        if suffix == "bat":
            assert script.startswith("@echo off\nsetlocal DisableDelayedExpansion\n")
            assert "os.replace(temp, target)" in script
            assert marker < script.index("\n:story_only\n")
        else:
            assert 'mv -f "$MATERIALS_TMP" "$MATERIALS_STATE"' in script
            assert script.index("exit 0") < marker


def test_correction_launcher_delegates_interactive_target_to_python():
    root = Path(__file__).resolve().parents[2]
    launcher = root / "bat" / "doraemon" / "06_correct_subtitles.bat"
    data = launcher.read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")
    script = data.decode("utf-8").replace("\r\n", "\n")
    assert script.startswith("@echo off\nsetlocal DisableDelayedExpansion\n")
    assert "scripts\\correct_doraemon_subtitles.py --prompt-target" in script
    assert "set /p TARGET=" not in script
    assert "%TARGET%" not in script
    assert "scripts\\correct_doraemon_subtitles.py %*" in script


def test_materials_launchers_do_not_invoke_translation():
    root = Path(__file__).resolve().parents[2]
    for suffix in ("bat", "sh"):
        script = (root / "bat" / "doraemon" / f"最新话doraemon素材下载.{suffix}").read_text(encoding="utf-8")
        assert "scripts\\translate_doraemon.py" not in script
        assert "scripts/translate_doraemon.py" not in script
        assert "ensure_server" not in script and "stop_server" not in script


def make_cues(count: int) -> list[corrector.SourceCue]:
    return [
        corrector.SourceCue(
            index,
            f"00:00:{index:02d},000",
            f"00:00:{index + 1:02d},000",
            index * 1000,
            (index + 1) * 1000,
            f"行{index}",
        )
        for index in range(1, count + 1)
    ]


def test_parse_correction_lines_is_one_to_one():
    cues = make_cues(6)
    fixed, questions, consumed = corrector.parse_correction_lines("1|あ\n2|い\n3|う\n4|え\n5|お\n6|か", cues, 0, 6)
    assert [cue.source_ids for cue in fixed] == [(1,), (2,), (3,), (4,), (5,), (6,)]
    assert fixed[0].start == cues[0].start and fixed[0].end == cues[0].end
    assert consumed == 6
    assert questions == []


def test_parse_correction_lines_rejects_merging():
    # Merging changes the timeline the human re-splits by hand in JianYing.
    with pytest.raises(corrector.SubtitleCorrectionError, match="禁止合并"):
        corrector.parse_correction_lines("1-2|あ、い\n3|う", make_cues(6), 0, 3)


def test_parse_correction_lines_rejects_early_stop():
    # The exact failure that killed the 941 run: the model closes after one sentence.
    with pytest.raises(corrector.SubtitleCorrectionError, match="只处理到 3"):
        corrector.parse_correction_lines("1|ナマステ！\n2|のび太、\n3|インドに留学", make_cues(6), 0, 6)


def test_parse_correction_lines_rejects_gaps_and_noise():
    with pytest.raises(corrector.SubtitleCorrectionError, match="未连续覆盖"):
        corrector.parse_correction_lines("1|あ\n3|い", make_cues(6), 0, 3)
    with pytest.raises(corrector.SubtitleCorrectionError, match="无法解析"):
        corrector.parse_correction_lines("1|あ\n以上が結果です", make_cues(6), 0, 3)


def test_parse_correction_lines_keeps_question_lines_advisory():
    fixed, questions, consumed = corrector.parse_correction_lines(
        "1|あ。\n?2|「ア」と「イ」のどちらか判断できない\n2|い。\n3|う。\n4|え。\n?9|本批之外，丢弃",
        make_cues(4),
        0,
        4,
    )
    assert consumed == 4
    assert len(fixed) == 4
    assert [question.ids for question in questions] == [(2,)]
    assert questions[0].stage == "日文矫正"


def test_parse_correction_lines_fills_short_tail_with_raw_text():
    # 941 batches repeatedly stopped one cue short; the tail stays as raw ASR.
    fixed, questions, consumed = corrector.parse_correction_lines("1|あ\n2|い\n3|う", make_cues(4), 0, 4)
    assert consumed == 4
    assert [cue.source_ids for cue in fixed] == [(1,), (2,), (3,), (4,)]
    assert fixed[3].text == "行4"
    assert questions == []


def test_guard_correction_reverts_damage_but_keeps_fixes():
    # 941 run: head character dropped, and one kana swapped into a worse form.
    assert corrector.guard_correction("のびのび太だろ", "びのび太だろ") == "のびのび太だろ"
    assert corrector.guard_correction("勝させてあげよう", "勝らせてあげよう") == "勝させてあげよう"
    # Padding: the model pasted the next cue's words into a 2-character cue.
    assert corrector.guard_correction("よし留", "よし、留学先が登録された") == "よし留"
    assert corrector.guard_correction("インドの", "デリーに決まった") == "インドの"
    # 「相変わらず単」+「純だねギュミは」: the tail came from the next cue.
    assert (
        corrector.guard_correction("相変わらず単", "相変わらず単純だね", ("おっ", "純だねギュミは")) == "相変わらず単"
    )
    # Two cues replaced wholesale by neighbouring lines.
    assert corrector.guard_correction("よーし新しい", "僕を見つけるぞ") == "よーし新しい"
    assert corrector.guard_correction("ちょっと待ってよ", "一人じゃ無理無理") == "ちょっと待ってよ"
    # Real fixes survive: punctuation, kana->kanji, added characters.
    assert corrector.guard_correction("えっ二桁", "えっ、二桁") == "えっ、二桁"
    assert corrector.guard_correction("きたんでしょ", "来たんでしょ") == "来たんでしょ"
    assert corrector.guard_correction("ナマステ", "ナマステ！") == "ナマステ！"
    assert corrector.guard_correction("う", "うん") == "うん"
    assert corrector.guard_correction("のび", "のび太") == "のび太"


def test_correct_japanese_keeps_one_cue_when_model_damages_text(tmp_path):
    cues = make_cues(3)

    def chat(_settings, system, prompt, temperature=0.2):
        assert system == corrector.CORRECTION_SYSTEM_PROMPT
        return "1|行\n2|行\n3| 行"

    messages: list[str] = []
    pipeline = corrector.SubtitleCorrectionPipeline(
        TranslateSettings(project_dir=str(tmp_path), batch_size=12),
        chat_fn=chat,
        echo=messages.append,
    )
    fixed, questions = pipeline.correct_japanese(cues, {"title": "标题", "synopsis": "简介"})
    assert len(fixed) == 3
    assert fixed[2].text == "行3"
    assert questions == []


def test_repair_known_misreads_restores_nobita_homophone():
    # 941 cue 8: ASR wrote 「伸びた」 for 「のび太」.
    assert corrector.repair_known_misreads("伸びたんだも") == "のび太だも"
    assert corrector.repair_known_misreads("のびたんだもん") == "のび太だもん"
    # The real verb keeps its reading.
    assert corrector.repair_known_misreads("背が伸びたんだもん") == "背が伸びたんだもん"
    assert corrector.repair_known_misreads("のび太だも") == "のび太だも"


def test_correction_splits_batch_when_model_stops_early(tmp_path):
    cues = make_cues(12)
    messages: list[str] = []
    batch_ids: list[int] = []

    def chat(_settings, system, prompt, temperature=0.2):
        assert system == corrector.CORRECTION_SYSTEM_PROMPT
        start, end = (int(value) for value in re.search(r"ID (\d+)~(\d+)，必须全部处理", prompt).groups())
        batch_ids.append(end - start + 1)
        if end - start + 1 > 6:
            return f"{start}|あ。"  # model stops after the first sentence, twice
        return "\n".join(f"{index}|あ。" for index in range(start, end + 1))

    pipeline = corrector.SubtitleCorrectionPipeline(
        TranslateSettings(project_dir=str(tmp_path), batch_size=12),
        chat_fn=chat,
        echo=messages.append,
    )
    fixed, questions = pipeline.correct_japanese(cues, {"title": "标题", "synopsis": "简介"})
    assert [source_id for cue in fixed for source_id in cue.source_ids] == list(range(1, 13))
    assert questions == []
    assert batch_ids[:3] == [12, 12, 6]
    assert any("拆分" in message for message in messages)


def test_correct_japanese_repairs_homophone_the_model_left_alone(tmp_path):
    # 941 cue 8 came back untouched; the dry-run guard used to skip the repair.
    texts = ["だって", "伸びたんだも", "ん"]
    cues = [
        corrector.SourceCue(
            index, f"00:00:0{index},000", f"00:00:0{index + 1},000", index * 1000, (index + 1) * 1000, text
        )
        for index, text in enumerate(texts, 1)
    ]

    def chat(_settings, system, prompt, temperature=0.2):
        assert system == corrector.CORRECTION_SYSTEM_PROMPT
        return "1|だって\n2|伸びたんだも\n3|ん"

    messages: list[str] = []
    pipeline = corrector.SubtitleCorrectionPipeline(
        TranslateSettings(project_dir=str(tmp_path), batch_size=12),
        chat_fn=chat,
        echo=messages.append,
    )
    fixed, _questions = pipeline.correct_japanese(cues, {"title": "标题", "synopsis": "简介"})
    assert fixed[1].text == "のび太だも"
    assert any("人名读音修正 1 处" in message for message in messages)


def test_enforce_names_rewrites_speaker_swap():
    # The 941 regression: 「頼んだぞ、のび太」 came back as 「拜托了，胖虎」.
    assert corrector.enforce_names("頼んだぞ、のび太", "拜托了，胖虎") == "拜托了，大雄"
    assert corrector.enforce_names("のび太", "胖虎") == "大雄"


def test_enforce_names_keeps_output_when_giant_is_named():
    text = "ジャイアン、のび太を待ってる"
    assert corrector.enforce_names(text, "胖虎在等大雄") == "胖虎在等大雄"
    assert corrector.enforce_names(text, "胖虎在等某人") == "胖虎在等某人"


def test_enforce_names_ignores_unrelated_cues():
    assert corrector.enforce_names("ありがとう", "谢谢") == "谢谢"
    assert corrector.enforce_names("ドラえもん", "哆啦A梦") == "哆啦A梦"


def test_translate_chinese_applies_name_guard(tmp_path):
    fixed = [
        corrector.FixedCue((index,), "00:00:00,000", "00:00:01,000", text)
        for index, text in enumerate(["のび太", "ジャイアン", "のび太、大丈夫か"], 1)
    ]

    def chat(_settings, system, prompt, temperature=0.2):
        return json.dumps(
            {
                "translations": [
                    {"id": 1, "text": "胖虎"},
                    {"id": 2, "text": "胖虎"},
                    {"id": 3, "text": "胖虎，你没事吧"},
                ]
            }
        )

    messages: list[str] = []
    pipeline = corrector.SubtitleCorrectionPipeline(
        TranslateSettings(project_dir=str(tmp_path), batch_size=12),
        chat_fn=chat,
        echo=messages.append,
    )
    translations, _questions = pipeline.translate_chinese(fixed, {"title": "标题", "synopsis": "简介"})
    assert translations == ["大雄", "胖虎", "大雄，你没事吧"]
    assert any("人名归一 2 处" in message for message in messages)


def test_translation_splits_batch_when_reply_incomplete(tmp_path):
    fixed = [
        corrector.FixedCue((index,), f"00:00:{index:02d},000", f"00:00:{index + 1:02d},000", f"あ{index}")
        for index in range(1, 13)
    ]
    messages: list[str] = []

    def chat(_settings, system, prompt, temperature=0.2):
        assert system == corrector.TRANSLATION_SYSTEM_PROMPT
        section = prompt.split("【待翻译，共 ", 1)[1]
        count = int(section.split(" 条】", 1)[0])
        ids = [int(value) for value in re.findall(r"^(\d+)\. ", section, re.M)]
        assert len(ids) == count
        if count > 6:
            ids = ids[:1]
        return json.dumps({"translations": [{"id": index, "text": "中文译文。"} for index in ids]})

    pipeline = corrector.SubtitleCorrectionPipeline(
        TranslateSettings(project_dir=str(tmp_path), batch_size=12),
        chat_fn=chat,
        echo=messages.append,
    )
    translations, questions = pipeline.translate_chinese(fixed, {"title": "标题", "synopsis": "简介"})
    assert translations == ["中文译文。"] * 12
    assert questions == []
    assert any("拆分" in message for message in messages)
