from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import download_tver  # noqa: E402
import fetch_doraemon  # noqa: E402
import translate_doraemon  # noqa: E402


@pytest.mark.parametrize("name", [None, "941"])
def test_tver_download_disables_sidecars(tmp_path, monkeypatch, name):
    run = Mock()
    monkeypatch.setattr(download_tver.subprocess, "run", run)
    monkeypatch.setattr(download_tver, "_ffmpeg_location", lambda: None)

    download_tver.download("episode123", tmp_path / "materials", name=name)

    cmd = run.call_args.args[0]
    assert run.call_args.kwargs == {"check": True}
    assert "--no-write-thumbnail" in cmd
    assert "--no-write-info-json" in cmd
    assert not {"--write-thumbnail", "--write-info-json", "--convert-thumbnails"}.intersection(cmd)
    assert cmd[cmd.index("--remux-video") + 1] == "mp4"
    assert cmd[cmd.index("-o") + 1] == ("941.%(ext)s" if name else "%(title)s [%(id)s].%(ext)s")
    assert cmd[cmd.index("--paths") + 1] == str(tmp_path / "materials")


@pytest.mark.parametrize(
    ("title_html", "expected"),
    [
        ("「ひみつ道具」", "ひみつ道具"),
        ("ひみつ「道具」の冒険", "ひみつ道具の冒険"),
        ("&#12300;冒険&#12301;", "冒険"),
        ("冒険", "冒険"),
    ],
)
def test_parse_page_removes_corner_brackets_only_from_titles(title_html, expected):
    page = f"""
    <section>
    <p class="date">2026年11月7日放送</p><h1>「ドラえもん」</h1>
    <h2 class="story-title">{title_html}</h2><div class="read-box">
    <p class="txt_idt">「のび太」のお話。</p></div>
    </section>
    """

    data = fetch_doraemon.parse_page(page)

    assert data["episode_title"] == "ドラえもん"
    assert data["stories"][0]["title"] == expected
    assert data["stories"][0]["synopsis"] == "「のび太」のお話。"


@pytest.mark.parametrize("cover_mode", ["unavailable", "failed", "upscaled"])
def test_fetch_and_translate_use_only_desktop_materials(tmp_path, monkeypatch, cover_mode):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_doraemon, "DESKTOP_DIR", tmp_path / "Desktop")
    monkeypatch.setattr(fetch_doraemon, "STATE_DIR", tmp_path / "logs")
    page = """
    <section>
    <p class="date">2026年11月7日放送</p><h1>ドラえもん</h1>
    <h2 class="story-title">「ひみつ道具」</h2><div class="read-box">
    <p class="story-img"><img src="/cover.jpg"></p>
    <p class="txt_idt">のび太のお話。</p></div>
    <h2 class="story-title">「冒険」</h2><div class="read-box">
    <p class="txt_idt">みんなで冒険。</p></div>
    </section>
    """

    def respond(request):
        if request.url.path == "/doraemon/story/0941/":
            return httpx.Response(200, text=page)
        assert request.url.path == "/cover.jpg"
        return httpx.Response(200, content=b"original image")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(fetch_doraemon.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(
        fetch_doraemon,
        "ensure_comfyui",
        lambda client: (None if cover_mode == "unavailable" else "http://localhost:8188", None),
    )

    def upscale(client, base_url, src, dest):
        if cover_mode == "upscaled":
            dest.write_bytes(b"upscaled image")
            return True
        return False

    monkeypatch.setattr(fetch_doraemon, "comfy_upscale", upscale)
    assert fetch_doraemon.fetch_one("0941") == 0

    materials = tmp_path / "Desktop" / "doraemon_941"
    expected_files = {"metadata.json", ".metadata.lock", "941_1.jpg"}
    cover = "941_1_4k.jpg" if cover_mode == "upscaled" else "941_1.jpg"
    if cover_mode == "upscaled":
        expected_files.add(cover)
    assert {p.name for p in materials.iterdir()} == expected_files
    metadata = json.loads((materials / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 2
    assert metadata["broadcast_date"] == "2026-11-07"
    assert [story["story_id"] for story in metadata["stories"]] == ["941_1", "941_2"]
    assert all(story["zh"] is None for story in metadata["stories"])
    assert [story["title"] for story in metadata["stories"]] == ["ひみつ道具", "冒険"]
    assert metadata["stories"][0]["image_file"] == "941_1.jpg"
    assert all("image" not in story for story in metadata["stories"])
    assert (materials / "941_1.jpg").read_bytes() == b"original image"
    assert (tmp_path / "logs" / ".last_fetched").read_text().strip() == "941"
    assert (tmp_path / "logs" / ".last_fetched_date").read_text().strip() == "2026-11-07"

    translate = Mock(return_value=("中文标题", "中文简介。"))
    monkeypatch.setattr(translate_doraemon, "load_settings", lambda: SimpleNamespace(translate=object()))
    monkeypatch.setattr(translate_doraemon, "translate_story", translate)
    monkeypatch.setattr(translate_doraemon, "generate_tags", lambda *args: ["哆啦A梦", "大雄"])
    for _ in range(2):
        translate_doraemon.main("0941")
    assert translate.call_count == 2
    assert translate.call_args_list[0].args[1:] == ("ひみつ道具", "のび太のお話。")
    assert {p.name for p in materials.iterdir()} == expected_files
    translated = json.loads((materials / "metadata.json").read_text(encoding="utf-8"))
    for story in translated["stories"]:
        assert story["zh"]["title"] == "中文标题"
        assert story["zh"]["synopsis"] == "中文简介。"
        assert story["publish"]["title"] == "哆啦a梦941 中文标题"
        assert story["publish"]["douyin"]["tags"] == ["哆啦A梦", "大雄"]
        assert story["publish"]["bilibili"]["tags"] == ["哆啦A梦", "大雄"]
    assert {p.name for p in tmp_path.iterdir()} == {"Desktop", "logs"}


def test_translate_requires_metadata_not_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_doraemon, "DESKTOP_DIR", tmp_path)
    materials = tmp_path / "doraemon_941"
    materials.mkdir()
    for name in ("941_1_zh.md", "941_1.md", "942_1_jp.md"):
        (materials / name).write_text("# 标题\n\n简介", encoding="utf-8")
    translate = Mock()
    monkeypatch.setattr(translate_doraemon, "translate_story", translate)
    with pytest.raises(SystemExit) as exc:
        translate_doraemon.main("941")
    assert exc.value.code == 1
    translate.assert_not_called()


@pytest.mark.parametrize("failure", ["translate", "tags"])
def test_translate_failure_keeps_processing_other_stories(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(fetch_doraemon, "DESKTOP_DIR", tmp_path)
    materials = tmp_path / "doraemon_941"
    materials.mkdir()
    path = materials / "metadata.json"
    path.write_text(
        json.dumps({"episode": "0941", "stories": [{"title": "日文标题", "synopsis": "日文简介"}] * 2}),
        encoding="utf-8",
    )
    translate = Mock(return_value=("中文标题", "中文简介"))
    tags = Mock(return_value=["哆啦A梦"])
    if failure == "translate":
        translate.side_effect = [ValueError("translation failed"), ("中文标题", "中文简介")]
    else:
        tags.side_effect = [ValueError("tags failed"), ["哆啦A梦"]]
    monkeypatch.setattr(translate_doraemon, "load_settings", lambda: SimpleNamespace(translate=object()))
    monkeypatch.setattr(translate_doraemon, "translate_story", translate)
    monkeypatch.setattr(translate_doraemon, "generate_tags", tags)

    with pytest.raises(SystemExit) as exc:
        translate_doraemon.main("941")
    assert exc.value.code == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["stories"][1]["zh"]["title"] == "中文标题"
    assert (data["stories"][0]["zh"] is not None) == (failure == "tags")
    if failure == "tags":
        assert data["stories"][0]["publish"]["douyin"]["tags"] == []
    assert not list(materials.glob("*.md"))


def test_translation_is_saved_before_tags_and_reused_after_tag_failure(tmp_path, monkeypatch):
    from creator_agent.doraemon.metadata import load_metadata, save_sources

    path = tmp_path / "metadata.json"
    save_sources(path, {"episode": "941", "stories": [{"title": "日文标题", "synopsis": "日文简介"}]})
    translate = Mock(return_value=("中文标题", "第一段。\n\n第二段。"))
    monkeypatch.setattr(translate_doraemon, "translate_story", translate)

    def fail_tags(*args):
        assert load_metadata(path)["stories"][0]["zh"]["synopsis"] == "第一段。\n\n第二段。"
        raise RuntimeError("tag service unavailable")

    monkeypatch.setattr(translate_doraemon, "generate_tags", fail_tags)
    assert not translate_doraemon.translate_metadata(path, "941_1", object())[2]
    monkeypatch.setattr(translate_doraemon, "generate_tags", lambda *args: ["哆啦A梦", "大雄"])
    assert translate_doraemon.translate_metadata(path, "941_1", object())[2]
    assert translate.call_count == 1
    assert load_metadata(path)["stories"][0]["publish"]["douyin"]["tags"] == ["哆啦A梦", "大雄"]
    assert translate_doraemon.translate_metadata(path, "941_1", object(), refresh=True)[2]
    assert translate.call_count == 2
