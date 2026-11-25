"""Temporary 剪映 drafts must be cleaned up with visible success/failure messages."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from creator_agent.doraemon import jianying_cover as cover


@pytest.fixture
def draft_env(tmp_path, monkeypatch):
    root = tmp_path / "drafts"
    root.mkdir()
    monkeypatch.setenv("JIANYING_DRAFT_ROOT", str(root))
    monkeypatch.setattr(cover, "stop_jianying", Mock())
    return root


def save_index(root, drafts):
    data = {
        "all_draft_store": [{"draft_id": draft.name, "draft_fold_path": str(draft)} for draft in drafts],
        "draft_ids": [draft.name for draft in drafts],
    }
    index = root / "root_meta_info.json"
    index.write_text(json.dumps(data), encoding="utf-8")
    return index


def test_cleanup_reports_deleted_draft_and_prunes_index(draft_env, capsys):
    draft = draft_env / "CVP_941_123456"
    draft.mkdir()
    (draft / "partial.json").write_text("{}", encoding="utf-8")
    template = draft_env / "my_template"
    template.mkdir()
    index = save_index(draft_env, [draft, template])

    cover._cleanup_draft(draft)

    assert not draft.exists()
    assert template.is_dir()
    data = json.loads(index.read_text(encoding="utf-8"))
    assert data["draft_ids"] == [template.name]
    assert [entry["draft_id"] for entry in data["all_draft_store"]] == [template.name]
    output = capsys.readouterr().out
    assert f"临时草稿删除成功：{draft}" in output
    assert "草稿列表索引清理成功（移除 1 条失效记录）" in output
    assert "[错误]" not in output


def test_cleanup_reports_missing_draft_and_still_prunes_index(draft_env, capsys):
    draft = draft_env / "CVP_941_123456"
    index = save_index(draft_env, [draft])

    cover._cleanup_draft(draft)

    assert "临时草稿无需删除（目录不存在）" in capsys.readouterr().out
    assert json.loads(index.read_text(encoding="utf-8"))["all_draft_store"] == []


def test_cleanup_reports_locked_draft_and_keeps_its_index(draft_env, monkeypatch, capsys):
    draft = draft_env / "CVP_941_123456"
    draft.mkdir()
    index = save_index(draft_env, [draft])
    monkeypatch.setattr(cover.shutil, "rmtree", Mock(side_effect=PermissionError("文件被占用")))

    cover._cleanup_draft(draft)

    assert draft.is_dir()
    assert json.loads(index.read_text(encoding="utf-8"))["draft_ids"] == [draft.name]
    output = capsys.readouterr().out
    assert f"[错误] 临时草稿删除失败：{draft}" in output
    assert "文件被占用" in output
    assert "手动删除" in output
    assert "删除成功" not in output


def test_cleanup_checks_directory_is_actually_gone(draft_env, monkeypatch, capsys):
    draft = draft_env / "CVP_941_123456"
    draft.mkdir()
    monkeypatch.setattr(cover.shutil, "rmtree", Mock())

    cover._cleanup_draft(draft)

    output = capsys.readouterr().out
    assert "[错误] 临时草稿删除失败" in output
    assert "删除后草稿目录仍然存在" in output
    assert "删除成功" not in output


def test_cleanup_still_deletes_when_stopping_jianying_raises(draft_env, monkeypatch, capsys):
    draft = draft_env / "CVP_941_123456"
    draft.mkdir()
    monkeypatch.setattr(cover, "stop_jianying", Mock(side_effect=OSError("taskkill failed")))

    cover._cleanup_draft(draft)

    assert not draft.exists()
    output = capsys.readouterr().out
    assert "[错误] 清理草稿前停止剪映失败：taskkill failed" in output
    assert "临时草稿删除成功" in output


@pytest.mark.parametrize("contents", ["not json", '{"all_draft_store": {}}'])
def test_cleanup_reports_invalid_index_after_deleting_draft(draft_env, capsys, contents):
    draft = draft_env / "CVP_941_123456"
    draft.mkdir()
    index = draft_env / "root_meta_info.json"
    index.write_text(contents, encoding="utf-8")

    cover._cleanup_draft(draft)

    assert not draft.exists()
    assert index.read_text(encoding="utf-8") == contents
    output = capsys.readouterr().out
    assert "临时草稿删除成功" in output
    assert "[错误] 草稿列表索引清理失败" in output
    assert str(index) in output


def test_cleanup_reports_index_write_failure(draft_env, monkeypatch, capsys):
    draft = draft_env / "CVP_941_123456"
    draft.mkdir()
    index = save_index(draft_env, [draft])
    original_write = Path.write_text

    def write_text(path, *args, **kwargs):
        if path == index:
            raise PermissionError("index locked")
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)

    cover._cleanup_draft(draft)

    assert not draft.exists()
    output = capsys.readouterr().out
    assert "[错误] 草稿列表索引清理失败" in output
    assert "index locked" in output


@pytest.fixture
def render_env(draft_env, tmp_path, monkeypatch):
    image = tmp_path / "source.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(cover, "requirements_problem", lambda: None)
    monkeypatch.setattr(cover.time, "strftime", lambda fmt: "123456")

    def build_draft(**kwargs):
        draft = draft_env / kwargs["name"]
        draft.mkdir()
        (draft / "partial.json").write_text("{}", encoding="utf-8")
        return draft

    monkeypatch.setattr(cover, "build_draft", build_draft)
    monkeypatch.setattr(cover, "_open_and_export", lambda *args: tmp_path / "export.mp4")
    monkeypatch.setattr(cover, "_first_frame", lambda video, out: out)
    return {"image": image, "episode": "941", "title": "故事", "out": tmp_path / "final.jpg"}


@pytest.mark.parametrize("orientation, prefix", [("portrait", "CVP"), ("landscape", "CVL")])
def test_render_cleans_partial_draft_when_build_fails(render_env, draft_env, monkeypatch, capsys, orientation, prefix):
    build = cover.build_draft
    error = RuntimeError("photo preparation failed")

    def fail_build(**kwargs):
        build(**kwargs)
        raise error

    monkeypatch.setattr(cover, "build_draft", fail_build)

    with pytest.raises(RuntimeError) as caught:
        cover.render_cover(**render_env, orientation=orientation)

    assert caught.value is error
    assert not (draft_env / f"{prefix}_941_123456").exists()
    assert "临时草稿删除成功" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["_open_and_export", "_first_frame"])
def test_render_cleans_draft_when_export_or_frame_fails(render_env, draft_env, monkeypatch, capsys, stage):
    error = cover.CoverRenderError("render failed")
    monkeypatch.setattr(cover, stage, Mock(side_effect=error))

    with pytest.raises(cover.CoverRenderError) as caught:
        cover.render_cover(**render_env)

    assert caught.value is error
    assert not (draft_env / "CVP_941_123456").exists()
    assert "临时草稿删除成功" in capsys.readouterr().out


def test_cleanup_failure_does_not_mask_render_failure(render_env, draft_env, monkeypatch, capsys):
    error = cover.CoverRenderError("export failed")
    monkeypatch.setattr(cover, "_open_and_export", Mock(side_effect=error))
    real_rmtree = cover.shutil.rmtree
    draft = draft_env / "CVP_941_123456"

    def rmtree(path, *args, **kwargs):
        if Path(path) == draft:
            raise PermissionError("draft locked")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(cover.shutil, "rmtree", rmtree)

    with pytest.raises(cover.CoverRenderError) as caught:
        cover.render_cover(**render_env)

    assert caught.value is error
    assert draft.is_dir()
    assert "[错误] 临时草稿删除失败" in capsys.readouterr().out


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_finished_cover_is_kept_even_when_cleanup_fails(render_env, draft_env, monkeypatch, capsys, cleanup_fails):
    image = MagicMock()
    image.__enter__.return_value = image
    image.convert.return_value.save.side_effect = lambda path, **kwargs: path.write_bytes(b"finished cover")
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=SimpleNamespace(open=lambda path: image)))
    draft = draft_env / "CVP_941_123456"
    real_rmtree = cover.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if cleanup_fails and Path(path) == draft:
            raise PermissionError("draft locked")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(cover.shutil, "rmtree", rmtree)

    assert cover.render_cover(**render_env) == render_env["out"]
    assert render_env["out"].read_bytes() == b"finished cover"
    assert draft.exists() == cleanup_fails
    output = capsys.readouterr().out
    expected = "[错误] 临时草稿删除失败" if cleanup_fails else "临时草稿删除成功"
    assert expected in output
