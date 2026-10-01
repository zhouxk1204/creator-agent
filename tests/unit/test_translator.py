from __future__ import annotations

from unittest import mock

import pytest

from creator_agent.config import TranslateSettings
from creator_agent.models.transcript import TranscriptSegment
from creator_agent.translate.translator import (
    SrtTranslator,
    TranslationMismatchError,
    build_prompt,
    make_batches,
    parse_translation,
    process_translations,
)


def _seg(text: str, start: float = 0.0, end: float = 1.0) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def _translator() -> SrtTranslator:
    return SrtTranslator(TranslateSettings(batch_size=3, context_cues=2))


# ---------------------------------------------------------------------------
# batching / prompt / parsing (pure)
# ---------------------------------------------------------------------------


def test_make_batches():
    assert make_batches(7, 3) == [(0, 3), (3, 6), (6, 7)]
    assert make_batches(0, 3) == []
    assert make_batches(5, 0) == []


def test_build_prompt_numbers_and_context():
    segs = [_seg(f"文{i}") for i in range(6)]
    prompt = build_prompt(segs, 2, 5, context_cues=2)
    assert "背景参考" in prompt
    assert "1. 文0" in prompt and "2. 文1" in prompt  # context keeps cue numbers
    assert "3. 文2" in prompt and "5. 文4" in prompt
    assert "6. 文5" not in prompt
    assert "共 3 条" in prompt


def test_build_prompt_first_batch_has_no_context():
    prompt = build_prompt([_seg("あ"), _seg("い")], 0, 2)
    assert "背景参考" not in prompt
    assert "1. あ" in prompt and "2. い" in prompt


def test_parse_translation_happy():
    reply = "1. 你好\n2. 世界\n3. 再见"
    assert parse_translation(reply, 0, 3) == ["你好", "世界", "再见"]


def test_parse_translation_ignores_garbage_lines():
    reply = "好的，以下是翻译：\n1. 你好\n\n2. 世界\n（完）"
    assert parse_translation(reply, 0, 2) == ["你好", "世界"]


def test_parse_translation_offset_batch():
    reply = "4. 四\n5. 五"
    assert parse_translation(reply, 3, 5) == ["四", "五"]


def test_parse_translation_missing_raises():
    with pytest.raises(TranslationMismatchError, match="missing"):
        parse_translation("1. 你好", 0, 2)


def test_parse_translation_duplicate_raises():
    with pytest.raises(TranslationMismatchError, match="twice"):
        parse_translation("1. 你好\n1. 喂\n2. 世界", 0, 2)


def test_parse_translation_accepts_cjk_separators():
    assert parse_translation("1、你好\n2．世界", 0, 2) == ["你好", "世界"]


# ---------------------------------------------------------------------------
# translator with mocked LLM
# ---------------------------------------------------------------------------


def _reply_for(segs, start, end):
    return "\n".join(f"{i + 1}. 译{i}" for i in range(start, end))


def test_translate_segments_batches_and_preserves_order():
    segs = [_seg(f"文{i}") for i in range(7)]
    t = _translator()
    calls = []

    def fake_chat(user):
        calls.append(user)
        # figure out the batch range from the prompt's numbered lines
        nums = [int(ln.split(".")[0]) for ln in user.splitlines() if ln[:1].isdigit()]
        return "\n".join(f"{n}. 译{n}" for n in nums if "【待翻译" not in user or True)

    with mock.patch.object(SrtTranslator, "_chat", side_effect=fake_chat):
        zh = t.translate_segments(segs)
    # batch_size=3 -> 3 batches; numbering preserved end-to-end
    assert len(calls) == 3
    assert zh[0] == "译1" and zh[3] == "译4" and zh[6] == "译7"


def test_translate_segments_retry_on_mismatch():
    segs = [_seg("a"), _seg("b")]
    t = _translator()
    replies = iter(["1. 只有一条", "1. 甲\n2. 乙"])
    with mock.patch.object(SrtTranslator, "_chat", side_effect=lambda _: next(replies)):
        assert t.translate_segments(segs) == ["甲", "乙"]


def test_translate_segments_retries_japanese_echo():
    # A reply that is still Japanese counts as a failure and is retried.
    segs = [_seg("a"), _seg("b"), _seg("c")]
    t = _translator()
    replies = iter(["1. あああ\n2. いいい\n3. ううう", "1. 甲\n2. 乙\n3. 丙"])
    with mock.patch.object(SrtTranslator, "_chat", side_effect=lambda _: next(replies)):
        assert t.translate_segments(segs) == ["甲", "乙", "丙"]


def test_translate_segments_recursive_split_then_original_fallback():
    segs = [_seg("a"), _seg("b"), _seg("c")]
    t = _translator()

    # Full batch fails twice; halves: [0,1) succeeds, [1,3) fails twice,
    # then singles: cue2 ok, cue3 keeps original.
    def fake_chat(user):
        body = user.split("【待翻译", 1)[1]  # ignore reference-context lines
        if "1. a" in body and "3. c" in body:
            return "garbage"
        if "1. a" in body:
            return "1. 甲"
        if "2. b" in body and "3. c" in body:
            return "garbage"
        if "2. b" in body:
            return "2. 乙"
        return "garbage"  # cue 3 single always fails

    with mock.patch.object(SrtTranslator, "_chat", side_effect=fake_chat):
        zh = t.translate_segments(segs)
    assert zh == ["甲", "乙", "c"]


def test_translate_srt_roundtrip_timeline(tmp_path):
    from creator_agent.asr.subtitle import parse_srt, write_srt

    segs = [_seg("おはよう", 1.5, 3.25), _seg("さようなら", 4.0, 5.0)]
    src = tmp_path / "ep.srt"
    write_srt(segs, src)
    t = _translator()
    with mock.patch.object(SrtTranslator, "_chat", return_value="1. 早上好\n2. 再见"):
        out = t.translate_srt(src, tmp_path / "ep.zh.srt")
    zh = parse_srt(out)
    assert [(s.start, s.end) for s in zh] == [(1.5, 3.25), (4.0, 5.0)]
    assert [s.text for s in zh] == ["早上好", "再见"]


def test_translate_video_srt_skips_existing(tmp_path):
    video = tmp_path / "ep.mp4"
    video.write_bytes(b"x")
    (tmp_path / "ep.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nあ\n", encoding="utf-8")
    zh = tmp_path / "ep.zh.srt"
    zh.write_text("1\n00:00:00,000 --> 00:00:01,000\n啊\n", encoding="utf-8")

    from creator_agent.translate.translator import translate_video_srt

    with mock.patch.object(SrtTranslator, "_chat") as chat:
        path, err = translate_video_srt(video, TranslateSettings())
    assert err is None and path == zh
    chat.assert_not_called()  # existing .zh.srt -> no LLM call


def test_process_translations_burns(tmp_path):
    video = tmp_path / "ep.mp4"
    video.write_bytes(b"x")
    (tmp_path / "ep.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nあ\n", encoding="utf-8")

    with (
        mock.patch.object(SrtTranslator, "_chat", return_value="1. 啊"),
        mock.patch("creator_agent.translate.burner.burn_subtitles") as burn,
    ):
        done, failed = process_translations([video], TranslateSettings(), burn=True)
    assert done == 1 and failed == []
    burn.assert_called_once()
    assert burn.call_args.args[2].name == "ep.zh.mp4"
