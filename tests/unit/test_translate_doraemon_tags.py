"""Unit tests for the tag-generation parsing in scripts/translate_doraemon.py.

No LLM calls — only parse_tags() on canned model replies.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from translate_doraemon import parse_tags  # noqa: E402

GOOD_REPLY = "标签：#哆啦A梦 #新番 #童年动画 #大雄 #时光机 #大雄逆袭 #静香的秘密 #胖虎的歌声"


def test_parse_tags_happy_path():
    tags = parse_tags(GOOD_REPLY)
    assert tags[0] == "哆啦A梦"
    assert len(tags) == 8
    assert all(t and "#" not in t for t in tags)


def test_parse_tags_accepts_fullwidth_colon_and_dedupes():
    reply = "标签：#哆啦A梦 #哆啦A梦 #大雄 #时光机 #新番 #静香 #胖虎"
    tags = parse_tags(reply)
    assert tags.count("哆啦A梦") == 1
    assert len(tags) == 6


def test_parse_tags_drops_generic_filler():
    reply = "标签：#动漫 #动画 #二次元 #哆啦A梦 #大雄 #时光机 #新番 #静香 #胖虎"
    tags = parse_tags(reply)
    assert "动漫" not in tags
    assert "二次元" not in tags
    assert len(tags) == 6


def test_parse_tags_drops_kana_and_overlong():
    reply = "标签：#ドラえもん #这个标签实在是太长太长太长啦 #哆啦A梦 #大雄 #时光机 #新番 #静香 #胖虎"
    tags = parse_tags(reply)
    assert "ドラえもん" not in tags
    assert all(len(t) <= 12 for t in tags)
    assert len(tags) == 6


def test_parse_tags_falls_back_to_any_hash_line():
    reply = "好的，以下是标签：\n#哆啦A梦 #大雄 #时光机 #新番 #静香 #胖虎 #童年动画"
    tags = parse_tags(reply)
    assert len(tags) == 7


def test_parse_tags_rejects_no_tag_line():
    with pytest.raises(ValueError, match="no 标签"):
        parse_tags("哆啦A梦 大雄 时光机")


def test_parse_tags_rejects_too_few_tags():
    with pytest.raises(ValueError, match="too few"):
        parse_tags("标签：#哆啦A梦 #大雄 #新番")


def test_parse_tags_caps_at_twelve():
    reply = "标签：" + " ".join(f"#标签{i}" for i in range(20))
    tags = parse_tags(reply)
    assert len(tags) == 12
