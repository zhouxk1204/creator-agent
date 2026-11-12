from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from creator_agent.doraemon.metadata import MetadataError, normalize_metadata, source_hash
from scripts import publish_dual


def save(folder, data):
    (folder / "metadata.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def materials(tmp_path):
    folder = tmp_path / "素材 !"
    folder.mkdir()
    data = normalize_metadata(
        {
            "episode": "0941",
            "stories": [{"title": f"物語{i}", "synopsis": f"物語{i}のあらすじ。"} for i in (1, 2)],
        }
    )
    for i, story in enumerate(data["stories"], 1):
        for name in (f"941_{i}_final.mp4", f"941_{i}.jpg", f"portrait_{i}.jpg", f"landscape_{i}.jpg"):
            (folder / name).write_bytes(b"local test asset")
        story["image_file"] = f"941_{i}.jpg"
        story["zh"] = {
            "title": f"故事{i}",
            "synopsis": f"故事{i}第一段。\n\n第二段。\n",
            "source_hash": source_hash(story),
        }
        story["publish"] = {
            "title": f"哆啦a梦941 故事{i}",
            "video_path": f"941_{i}_final.mp4",
            "douyin": {
                "tags": ["哆啦A梦", "大雄"],
                "cover_portrait_path": f"portrait_{i}.jpg",
                "cover_landscape_path": f"landscape_{i}.jpg",
            },
            "bilibili": {"tags": [f"标签{n}" for n in range(11)], "tid": 24},
        }
    save(folder, data)
    return folder, data


def job(folder, **overrides):
    args = dict(story="941_1", schedule=None, tid=None, douyin_account="default", bilibili_account="default", only=None)
    args.update(overrides)
    return publish_dual.PublishJob(folder, SimpleNamespace(**args))


def flag(command, name):
    return command[command.index(name) + 1]


def test_commands_use_story_metadata_and_preserve_bilibili_newlines(materials):
    folder, data = materials
    selected = job(folder, story="941_2")
    commands = dict(selected.build_commands(headed=True))
    douyin, bilibili = commands["抖音"], commands["B站"]
    for cmd in commands.values():
        assert flag(cmd, "--file") == str(folder / "941_2_final.mp4")
        assert flag(cmd, "--title") == "哆啦a梦941 故事2"
    assert flag(douyin, "--desc") == "#哆啦A梦 #大雄"
    assert flag(douyin, "--tags") == "哆啦A梦,大雄"
    assert flag(douyin, "--declaration") == "虚构演绎，仅供娱乐"
    assert flag(douyin, "--collection") == "哆啦A梦2026合集"
    assert flag(douyin, "--thumbnail-portrait") == str(folder / "portrait_2.jpg")
    assert flag(douyin, "--thumbnail-landscape") == str(folder / "landscape_2.jpg")
    assert "--headed" in douyin
    assert flag(bilibili, "--desc") == data["stories"][1]["zh"]["synopsis"]
    assert flag(bilibili, "--thumbnail") == str(folder / "941_2.jpg")
    assert len(flag(bilibili, "--tags").split(",")) == 11
    assert flag(bilibili, "--tid") == "24"


def test_explicit_story_required_for_multiple_stories(materials):
    folder, data = materials
    with pytest.raises(MetadataError, match="--story"):
        job(folder, story=None)
    with pytest.raises(MetadataError, match="没有故事"):
        job(folder, story="942_1")
    data["stories"] = data["stories"][:1]
    save(folder, data)
    assert job(folder, story=None).story_id == "941_1"


@pytest.mark.parametrize("platform, count", [("douyin", 6), ("bilibili", 12)])
def test_platform_tag_limits(materials, platform, count):
    folder, data = materials
    data["stories"][0]["publish"][platform]["tags"] = [f"标签{i}" for i in range(count)]
    save(folder, data)
    with pytest.raises(MetadataError, match="标签必须"):
        job(folder)


@pytest.mark.parametrize("tags", [[], ["#大雄"], ["大雄", "大雄"], ["大 雄"], "大雄"])
def test_invalid_tags_fail_before_upload(materials, tags):
    folder, data = materials
    data["stories"][0]["publish"]["douyin"]["tags"] = tags
    save(folder, data)
    with pytest.raises(MetadataError):
        job(folder)


def test_only_selected_platform_assets_are_required(materials):
    folder, data = materials
    data["stories"][0]["publish"]["douyin"] = {}
    save(folder, data)
    assert len(job(folder, only="bilibili").build_commands(False)) == 1
    with pytest.raises(MetadataError):
        job(folder)


def test_no_video_or_cover_fallbacks(materials):
    folder, data = materials
    publish = data["stories"][0]["publish"]
    publish["video_path"] = None
    save(folder, data)
    with pytest.raises(MetadataError, match="路径未填写"):
        job(folder)
    publish["video_path"] = "941_1_final.mp4"
    publish["douyin"]["cover_portrait_path"] = None
    (folder / "cover.jpg").write_bytes(b"old cover")
    save(folder, data)
    with pytest.raises(MetadataError, match="路径未填写"):
        job(folder)
    publish["douyin"]["cover_portrait_path"] = publish["douyin"]["cover_landscape_path"]
    save(folder, data)
    with pytest.raises(MetadataError, match="独立文件"):
        job(folder)


def test_old_publish_package_is_not_supported(tmp_path):
    (tmp_path / "title.txt").write_text("旧标题", encoding="utf-8")
    (tmp_path / "video.mp4").write_bytes(b"old video")
    with pytest.raises(MetadataError, match="metadata.json"):
        job(tmp_path)


def test_stale_translation_is_not_publishable(materials):
    folder, data = materials
    data["stories"][0]["synopsis"] = "更新したあらすじ。"
    save(folder, data)
    with pytest.raises(MetadataError, match="过期"):
        job(folder)


def test_absolute_asset_paths_and_overrides(materials):
    folder, data = materials
    data["stories"][0]["publish"]["video_path"] = str(folder / "941_1_final.mp4")
    save(folder, data)
    selected = job(folder / "metadata.json", tid="25", schedule="2026-12-01 10:15", douyin_account="main")
    douyin, bilibili = [cmd for _, cmd in selected.build_commands(False)]
    assert flag(douyin, "--account") == "main"
    assert flag(douyin, "--schedule") == "2026-12-01 10:15"
    assert "--headless" in douyin
    assert flag(bilibili, "--tid") == "25"


def test_dry_run_does_not_publish_or_require_sau(materials, monkeypatch):
    folder, _ = materials
    run = Mock(side_effect=AssertionError("must not upload"))
    monkeypatch.setattr(publish_dual.subprocess, "run", run)
    monkeypatch.setattr(publish_dual, "sau_exe", lambda: folder / "not-installed")
    monkeypatch.setattr(sys, "argv", ["publish_dual.py", str(folder), "--story", "941_1", "--dry-run"])
    assert publish_dual.main() == 0
    run.assert_not_called()


@pytest.mark.parametrize("answer", ["n", "", "yes"])
def test_publish_requires_explicit_y(materials, monkeypatch, answer):
    folder, _ = materials
    executable = folder / "sau"
    executable.touch()
    monkeypatch.setattr(publish_dual, "sau_exe", lambda: executable)
    monkeypatch.setattr(sys, "argv", ["publish_dual.py", str(folder), "--story", "941_1"])
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    run = Mock(side_effect=AssertionError("must not upload"))
    monkeypatch.setattr(publish_dual.subprocess, "run", run)
    assert publish_dual.main() == 1
    run.assert_not_called()


def test_one_failed_platform_does_not_block_other(materials, monkeypatch):
    folder, _ = materials
    executable = folder / "sau"
    executable.touch()
    monkeypatch.setattr(publish_dual, "sau_exe", lambda: executable)
    monkeypatch.setattr(sys, "argv", ["publish_dual.py", str(folder), "--story", "941_1", "--yes"])
    run = Mock(side_effect=[OSError("upload error"), SimpleNamespace(returncode=0)])
    monkeypatch.setattr(publish_dual.subprocess, "run", run)
    assert publish_dual.main() == 1
    assert run.call_count == 2
    assert run.call_args_list[1].args[0][1] == "bilibili"


def test_saved_target_accepts_bom_and_quoted_override(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_dual, "REPO_ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/.last_doraemon_materials").write_text(str(tmp_path / "saved"), encoding="utf-8-sig")
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert publish_dual.choose_target(None, True) == tmp_path / "saved/metadata.json"
    monkeypatch.setattr("builtins.input", lambda prompt: f' "{tmp_path / "manual"}" ')
    assert publish_dual.choose_target(None, True) == tmp_path / "manual/metadata.json"


def test_publisher_imports_without_site_packages():
    script = Path(publish_dual.__file__).resolve()
    result = subprocess.run([sys.executable, "-S", str(script), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "--story" in result.stdout


@pytest.mark.parametrize("name", ["publish_douyin", "publish_dual"])
def test_publish_launchers_use_python_prompts_and_forward_arguments(name):
    path = Path(__file__).resolve().parents[2] / "bat" / f"{name}.bat"
    raw = path.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("ascii")
    assert "setlocal DisableDelayedExpansion" in text
    assert "--prompt-target" in text
    assert 'publish_dual.py" %*' in text
    assert "title.txt" not in text
