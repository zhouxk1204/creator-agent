from __future__ import annotations

import os

from creator_agent.translate.vault import export_subtitles, load_synopsis, parse_synopsis_md


def test_parse_synopsis_canonical_format():
    text = "# スケスケ望遠鏡でさがしもの\n\nパパから万年筆を…。\n話を聞いたドラえもんは…。\n"
    info = parse_synopsis_md(text)
    assert info["title"] == "スケスケ望遠鏡でさがしもの"
    assert info["synopsis"] == "パパから万年筆を…。\n話を聞いたドラえもんは…。"


def test_parse_synopsis_legacy_format_and_section_end():
    text = "# 标题 テスト\n## 简介\n第一行。\n第二行。\n## 其他\n不算简介。\n"
    info = parse_synopsis_md(text)
    assert info["title"] == "テスト"
    assert info["synopsis"] == "第一行。\n第二行。"


def test_load_synopsis_matches_hash_stem_to_underscore_note(tmp_path):
    syn_dir = tmp_path / "简介"
    syn_dir.mkdir()
    (syn_dir / "936_1.md").write_text("# タイトル\n\nあらすじ。\n", encoding="utf-8")
    info = load_synopsis(tmp_path, "936#1")
    assert info is not None and info["title"] == "タイトル" and info["synopsis"] == "あらすじ。"
    # Exact-stem note wins when both exist; missing note -> None.
    (syn_dir / "936#1.md").write_text("# 直撃\n\nちょく。\n", encoding="utf-8")
    assert load_synopsis(tmp_path, "936#1")["title"] == "直撃"
    assert load_synopsis(tmp_path, "999#9") is None
    assert load_synopsis("", "936#1") is None


def test_load_synopsis_truncates_long_synopsis(tmp_path):
    syn_dir = tmp_path / "简介"
    syn_dir.mkdir()
    (syn_dir / "a.md").write_text("# t\n\n" + "あ" * 600, encoding="utf-8")
    info = load_synopsis(tmp_path, "a")
    assert len(info["synopsis"]) == 501  # 500 chars + ellipsis
    assert info["synopsis"].endswith("…")


def test_export_subtitles_creates_per_stem_dir_and_copies(tmp_path):
    src = tmp_path / "inbox"
    src.mkdir()
    ja = src / "936#1.srt"
    zh = src / "936#1.zh.srt"
    ja.write_text("ja", encoding="utf-8")
    zh.write_text("zh", encoding="utf-8")
    out = export_subtitles(tmp_path / "vault", "936#1", [ja, zh, src / "missing.srt"])
    assert out == tmp_path / "vault" / "936#1"
    assert (out / "936#1.srt").read_text(encoding="utf-8") == "ja"
    assert (out / "936#1.zh.srt").read_text(encoding="utf-8") == "zh"


def test_export_subtitles_never_overwrites_newer_target(tmp_path):
    src = tmp_path / "inbox"
    src.mkdir()
    ja = src / "a.srt"
    ja.write_text("old generated", encoding="utf-8")
    target_dir = tmp_path / "vault" / "a"
    target_dir.mkdir(parents=True)
    hand_edit = target_dir / "a.srt"
    hand_edit.write_text("hand edit", encoding="utf-8")
    # Target newer than source -> kept.
    os.utime(hand_edit, (ja.stat().st_mtime + 100, ja.stat().st_mtime + 100))
    export_subtitles(tmp_path / "vault", "a", [ja])
    assert hand_edit.read_text(encoding="utf-8") == "hand edit"
    # Source newer -> refreshed.
    os.utime(ja, (hand_edit.stat().st_mtime + 100, hand_edit.stat().st_mtime + 100))
    export_subtitles(tmp_path / "vault", "a", [ja])
    assert hand_edit.read_text(encoding="utf-8") == "old generated"
    assert export_subtitles("", "a", [ja]) is None  # blank project_dir = off
