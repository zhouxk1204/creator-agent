from __future__ import annotations

import json
from unittest import mock

import pytest

from creator_agent.config import TranslateSettings
from creator_agent.translate import learn
from creator_agent.translate.learn import (
    analyze_episode,
    build_classify_prompt,
    learn_project,
    parse_learning_json,
)
from creator_agent.translate.memory import MemoryStore


def _write_srt(path, cues):
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines.append(f"{i}\n00:00:0{start},000 --> 00:00:0{end},000\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_episode(tmp_path, name="935#1"):
    ep = tmp_path / "episodes" / name
    ep.mkdir(parents=True)
    _write_srt(ep / learn.JA_FILE, [(1, 2, "のび太くん"), (2, 3, "宿題をしなさい")])
    _write_srt(ep / learn.AI_FILE, [(1, 2, "野比太"), (2, 3, "必须要做作业")])
    _write_srt(ep / learn.FINAL_FILE, [(1, 2, "大雄"), (2, 3, "快写作业啦")])
    return ep


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_parse_learning_json_tolerates_prose():
    reply = '好的，分析如下：\n{"cases": [{"id": 1, "categories": ["语气错误"], "note": "x"}], "rules": ["r1"]}\n以上。'
    data = parse_learning_json(reply)
    assert data["cases"][0]["id"] == 1 and data["rules"] == ["r1"]


def test_parse_learning_json_fills_defaults():
    assert parse_learning_json("{}") == {"cases": [], "rules": []}


def test_parse_learning_json_no_json_raises():
    with pytest.raises(ValueError, match="no JSON"):
        parse_learning_json("分析结果：一切正常")


def test_build_classify_prompt_includes_flag():
    prompt = build_classify_prompt([{"id": 1, "ja": "日", "ai": "中", "final": "中2", "flag": "时间轴有修改"}])
    assert "#1（时间轴有修改）" in prompt
    assert "日文: 日" in prompt and "AI: 中" in prompt and "人工: 中2" in prompt


# ---------------------------------------------------------------------------
# episode analysis with mocked LLM
# ---------------------------------------------------------------------------

_LLM_REPLY = json.dumps(
    {
        "cases": [
            {"id": 1, "categories": ["专有名词错误"], "note": "人名译错", "term_ja": "のび太", "term_zh": "大雄"},
            {"id": 2, "categories": ["语气错误", "口语表达不自然"], "note": "语气生硬"},
            {"id": 99, "categories": ["漏译"], "note": "不存在的 id，应被忽略"},
        ],
        "rules": ["人名用约定译名", "口语句尾要自然"],
    },
    ensure_ascii=False,
)


def test_analyze_episode_full(tmp_path):
    ep = _make_episode(tmp_path)
    with mock.patch("creator_agent.translate.learn.chat", return_value=_LLM_REPLY):
        result = analyze_episode(ep, TranslateSettings())

    analysis = json.loads((ep / learn.ANALYSIS_FILE).read_text(encoding="utf-8"))
    assert analysis["episode"] == "935#1"
    assert analysis["summary"]["same"] == 2 and analysis["summary"]["text_changed"] == 2

    assert len(result["cases"]) == 2
    case1 = result["cases"][0]
    assert case1["categories"] == ["专有名词错误"] and case1["episode"] == "935#1"
    assert case1["ja"] == "のび太くん" and case1["final"] == "大雄"
    assert result["terms"] == [{"ja": "のび太", "zh": "大雄", "note": "人名译错"}]
    assert result["rules"] == ["人名用约定译名", "口语句尾要自然"]


def test_analyze_episode_llm_failure_degrades(tmp_path):
    ep = _make_episode(tmp_path)
    with mock.patch("creator_agent.translate.learn.chat", side_effect=RuntimeError("server down")):
        result = analyze_episode(ep, TranslateSettings())
    assert result["cases"] == [] and result["rules"] == []
    assert (ep / learn.ANALYSIS_FILE).exists()  # analysis still written


def test_analyze_episode_missing_files_raises(tmp_path):
    ep = tmp_path / "episodes" / "empty"
    ep.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="missing"):
        analyze_episode(ep, TranslateSettings())


def test_analyze_episode_structure_only_change(tmp_path):
    ep = tmp_path / "episodes" / "ep2"
    ep.mkdir(parents=True)
    _write_srt(ep / learn.JA_FILE, [(1, 2, "あ"), (2, 3, "い")])
    _write_srt(ep / learn.AI_FILE, [(1, 2, "甲"), (2, 3, "乙")])
    # human merged two cues into one, same text
    _write_srt(ep / learn.FINAL_FILE, [(1, 3, "甲 乙")])
    with mock.patch("creator_agent.translate.learn.chat", return_value='{"cases": [], "rules": []}'):
        result = analyze_episode(ep, TranslateSettings())
    assert result["findings"][0]["kind"] == "merge"
    assert result["analysis"]["summary"]["merge"] == 1


# ---------------------------------------------------------------------------
# project level
# ---------------------------------------------------------------------------


def test_learn_project_end_to_end_and_idempotent(tmp_path):
    proj = tmp_path
    _make_episode(proj, "935#1")
    _make_episode(proj, "935#2")
    settings = TranslateSettings()

    with mock.patch("creator_agent.translate.learn.chat", return_value=_LLM_REPLY):
        done, failed = learn_project(proj, settings)
    assert done == 2 and failed == []

    store = MemoryStore.load(proj)
    assert store is not None
    # both episodes have identical content -> dedupe keeps one copy of each case
    assert len(store.cases) == 2
    assert store.terms == [{"ja": "のび太", "zh": "大雄", "note": "人名译错"}]  # deduped
    assert store.rules == ["人名用约定译名", "口语句尾要自然"]  # deduped

    # compiled bundle + reports exist
    knowledge = proj / "knowledge"
    compiled = json.loads((knowledge / "translation_memory.json").read_text(encoding="utf-8"))
    assert compiled["glossary"][0]["zh"] == "大雄"
    assert (proj / "reports" / "episode_report.csv").exists()
    html = (proj / "reports" / "project_report.html").read_text(encoding="utf-8")
    assert "专有名词错误" in html and "大雄" in html

    # re-learn: no duplicates
    with mock.patch("creator_agent.translate.learn.chat", return_value=_LLM_REPLY):
        learn_project(proj, settings)
    store2 = MemoryStore.load(proj)
    assert len(store2.cases) == 2 and len(store2.terms) == 1


def test_learn_project_no_episodes(tmp_path):
    done, failed = learn_project(tmp_path, TranslateSettings())
    assert done == 0 and failed == []


# ---------------------------------------------------------------------------
# memory injection into translation
# ---------------------------------------------------------------------------


def test_translator_injects_memory(tmp_path):
    from creator_agent.models.transcript import TranscriptSegment
    from creator_agent.translate.translator import SrtTranslator

    store = MemoryStore.load_or_empty(tmp_path)
    store.add_terms([{"ja": "のび太", "zh": "大雄", "note": "人名"}])
    store.save()

    settings = TranslateSettings(project_dir=str(tmp_path), batch_size=10)
    segs = [TranscriptSegment(start=0, end=1, text="のび太くん、宿題をしなさい")]
    t = SrtTranslator(settings)
    with mock.patch.object(SrtTranslator, "_chat", return_value="1. 大雄，快写作业") as chat_mock:
        zh = t.translate_segments(segs)
    assert zh == ["大雄，快写作业"]
    user_msg = chat_mock.call_args.args[0]
    assert "术语库" in user_msg and "のび太 → 大雄" in user_msg
