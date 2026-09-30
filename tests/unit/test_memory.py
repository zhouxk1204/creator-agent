from __future__ import annotations

import json

from creator_agent.translate.memory import COMPILED, ERROR_CASES, RULES_MD, TERMINOLOGY, MemoryStore


def _store(tmp_path):
    return MemoryStore(tmp_path / "knowledge")


def test_load_returns_none_when_nothing_learned(tmp_path):
    assert MemoryStore.load(tmp_path) is None
    (tmp_path / "knowledge").mkdir()
    assert MemoryStore.load(tmp_path) is None  # empty dir still None


def test_add_cases_dedupes_by_ja_and_ai(tmp_path):
    s = _store(tmp_path)
    case = {"ja": "あ", "ai": "啊", "final": "呀", "categories": ["语气错误"]}
    assert s.add_cases([case, dict(case)]) == 1
    assert s.add_cases([dict(case)]) == 0  # re-learn is idempotent
    assert s.add_cases([{"ja": "い", "ai": "咿", "final": "咦"}]) == 1
    assert len(s.cases) == 2


def test_add_terms_human_correction_wins(tmp_path):
    s = _store(tmp_path)
    s.add_terms([{"ja": "のび太", "zh": "野比", "note": "人名"}])
    assert s.add_terms([{"ja": "のび太", "zh": "大雄", "note": ""}]) == 0
    assert s.terms[0]["zh"] == "大雄"  # updated, not duplicated
    assert s.terms[0]["note"] == "人名"  # old note survives blank update


def test_add_rules_dedupes_and_strips(tmp_path):
    s = _store(tmp_path)
    assert s.add_rules(["- 短回应独立成条", "短回应独立成条", "  "]) == 1
    assert s.rules == ["短回应独立成条"]


def test_save_and_reload_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.add_cases([{"ja": "あ", "ai": "啊", "final": "呀", "categories": ["直译"], "note": "n"}])
    s.add_terms([{"ja": "ドラえもん", "zh": "哆啦A梦", "note": "人名"}])
    s.add_rules(["口语不要直译"])
    s.save()

    kdir = tmp_path / "knowledge"
    assert json.loads((kdir / ERROR_CASES).read_text(encoding="utf-8"))["cases"][0]["final"] == "呀"
    assert json.loads((kdir / TERMINOLOGY).read_text(encoding="utf-8"))["terms"][0]["zh"] == "哆啦A梦"
    assert "- 口语不要直译" in (kdir / RULES_MD).read_text(encoding="utf-8")
    compiled = json.loads((kdir / COMPILED).read_text(encoding="utf-8"))
    assert compiled["glossary"][0]["ja"] == "ドラえもん"

    s2 = MemoryStore.load(tmp_path)
    assert s2 is not None
    assert len(s2.cases) == 1 and len(s2.terms) == 1 and s2.rules == ["口语不要直译"]


def test_prompt_section_hits_glossary_and_similar_cases(tmp_path):
    s = _store(tmp_path)
    s.add_terms([{"ja": "のび太", "zh": "大雄"}, {"ja": "ジャイアン", "zh": "胖虎"}])
    s.add_cases(
        [
            {
                "ja": "のび太くん、宿題をしなさい",
                "ai": "野比太，做作业",
                "final": "大雄，快写作业",
                "categories": ["专有名词错误"],
            },
            {"ja": "全然関係ない文", "ai": "x", "final": "y", "categories": ["漏译"]},
        ]
    )
    s.add_rules(["人名保持统一"])

    section = s.prompt_section("のび太くん、宿題をしたの？", max_cases=5)
    assert "のび太 → 大雄" in section
    assert "ジャイアン" not in section  # term not in batch -> not injected
    assert "宿題をしなさい" in section  # similar case retrieved
    assert "全然関係ない文" not in section  # unrelated case excluded
    assert "人名保持统一" in section


def test_prompt_section_empty_store_returns_empty(tmp_path):
    assert _store(tmp_path).prompt_section("何でも") == ""
