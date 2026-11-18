from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from creator_agent.doraemon.metadata import update_metadata

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import fetch_doraemon as fetch  # noqa: E402

PAGE = """
<section>
<p class="date">2026年11月7日放送</p><h1>ドラえもん</h1>
<h2 class="story-title">「冒険」</h2><div class="read-box">
<p class="story-img"><img src="/cover.jpg"></p>
<p class="txt_idt">みんなで冒険。</p></div>
</section>
"""


@pytest.fixture
def covers(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "DESKTOP_DIR", tmp_path)
    monkeypatch.setattr(fetch, "STATE_DIR", tmp_path / "logs")
    monkeypatch.setattr(fetch, "FORCE", False)
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/doraemon/story/0941/":
            return httpx.Response(200, text=PAGE)
        assert request.url.path == "/cover.jpg"
        return httpx.Response(200, content=b"source image")

    client_class = httpx.Client
    monkeypatch.setattr(fetch.httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(fetch, "ensure_comfyui", lambda client: ("http://localhost:8188", None))

    def upscale(client, base, source, dest):
        assert source.read_bytes() == b"source image"
        dest.write_bytes(b"upscaled image")
        return True

    monkeypatch.setattr(fetch, "comfy_upscale", upscale)
    return tmp_path / "doraemon_941", requests


def test_refetch_reuses_upscaled_cover_without_redownloading_and_preserves_edits(covers, monkeypatch):
    folder, requests = covers
    assert fetch.fetch_one("941") == 0
    assert not (folder / "941_1.jpg").exists()

    def edit(data):
        data["custom"] = "keep"
        data["stories"][0]["publish"]["video_path"] = "manual.mp4"

    update_metadata(folder / "metadata.json", edit)
    ensure = Mock()
    monkeypatch.setattr(fetch, "ensure_comfyui", ensure)
    assert fetch.fetch_one("0941") == 0
    assert requests.count("/cover.jpg") == 1
    ensure.assert_not_called()
    data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert data["stories"][0]["image_file"] == "941_1_4k.jpg"
    assert data["stories"][0]["publish"]["video_path"] == "manual.mp4"
    assert data["custom"] == "keep"
    assert not (folder / "941_1.jpg").exists()


def test_preexisting_upscale_is_selected_and_original_removed_after_metadata_save(covers):
    folder, requests = covers
    folder.mkdir()
    (folder / "941_1.jpg").write_bytes(b"stale source")
    (folder / "941_1_4k.jpg").write_bytes(b"upscaled")
    assert fetch.fetch_one("941") == 0
    assert "/cover.jpg" not in requests
    assert not (folder / "941_1.jpg").exists()
    assert json.loads((folder / "metadata.json").read_text())["stories"][0]["image_file"] == "941_1_4k.jpg"


def test_metadata_failure_keeps_original_cover(tmp_path, monkeypatch):
    source = tmp_path / "941_1.jpg"
    source.write_bytes(b"source")
    upscaled = tmp_path / "941_1_4k.jpg"
    upscaled.write_bytes(b"upscaled")
    monkeypatch.setattr(fetch, "update_metadata", Mock(side_effect=OSError("cannot save metadata")))
    with pytest.raises(OSError, match="cannot save metadata"):
        fetch._promote_cover(tmp_path / "metadata.json", "941_1", source, upscaled)
    assert source.read_bytes() == b"source"


def test_failed_force_upscale_keeps_original_as_metadata_cover(covers, monkeypatch):
    folder, requests = covers
    assert fetch.fetch_one("941") == 0
    monkeypatch.setattr(fetch, "FORCE", True)
    monkeypatch.setattr(fetch, "comfy_upscale", lambda *args: False)
    assert fetch.fetch_one("941") == 0
    assert requests.count("/cover.jpg") == 2
    assert (folder / "941_1.jpg").read_bytes() == b"source image"
    assert json.loads((folder / "metadata.json").read_text())["stories"][0]["image_file"] == "941_1.jpg"


@pytest.mark.parametrize("retry_succeeds", [False, True])
def test_ordinary_retry_after_failed_force_refresh_does_not_reuse_stale_upscale(covers, monkeypatch, retry_succeeds):
    folder, requests = covers
    assert fetch.fetch_one("941") == 0
    upscaled = folder / "941_1_4k.jpg"
    os.utime(upscaled, ns=(1, 1))
    monkeypatch.setattr(fetch, "FORCE", True)
    monkeypatch.setattr(fetch, "comfy_upscale", lambda *args: False)
    assert fetch.fetch_one("941") == 0
    source = folder / "941_1.jpg"
    assert source.is_file()

    retry = Mock()

    def upscale(client, base, src, dest):
        retry(src, dest)
        if retry_succeeds:
            dest.write_bytes(b"refreshed upscale")
        return retry_succeeds

    monkeypatch.setattr(fetch, "FORCE", False)
    monkeypatch.setattr(fetch, "comfy_upscale", upscale)
    assert fetch.fetch_one("941") == 0
    retry.assert_called_once_with(source, upscaled)
    assert requests.count("/cover.jpg") == 2
    data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert data["stories"][0]["image_file"] == (upscaled.name if retry_succeeds else source.name)
    assert source.exists() == (not retry_succeeds)
    assert upscaled.read_bytes() == (b"refreshed upscale" if retry_succeeds else b"upscaled image")


def test_empty_upscaled_cover_is_not_reused(covers):
    folder, requests = covers
    folder.mkdir()
    (folder / "941_1_4k.jpg").touch()
    assert fetch.fetch_one("941") == 0
    assert "/cover.jpg" in requests
    assert (folder / "941_1_4k.jpg").read_bytes() == b"upscaled image"
    assert not (folder / "941_1.jpg").exists()


def test_image_replace_failure_keeps_previous_image_and_cleans_temp(tmp_path, monkeypatch):
    dest = tmp_path / "cover.jpg"
    dest.write_bytes(b"previous image")
    monkeypatch.setattr(fetch.os, "replace", Mock(side_effect=OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        fetch._save_image(dest, b"new image")
    assert dest.read_bytes() == b"previous image"
    assert set(tmp_path.iterdir()) == {dest}


def test_empty_image_response_does_not_replace_existing_cover(tmp_path):
    dest = tmp_path / "cover.jpg"
    dest.write_bytes(b"previous image")
    with pytest.raises(ValueError, match="Empty image"):
        fetch._save_image(dest, b"")
    assert dest.read_bytes() == b"previous image"
    assert set(tmp_path.iterdir()) == {dest}


def test_resolve_latest_only_writes_state_without_fetching_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "STATE_DIR", tmp_path)
    monkeypatch.setattr(fetch, "_latest_item", lambda: {"id": "0941", "date": "2026-11-07"})
    fetch_one = Mock()
    monkeypatch.setattr(fetch, "fetch_one", fetch_one)
    assert fetch.resolve_latest_state() == 0
    fetch_one.assert_not_called()
    assert (tmp_path / ".last_fetched").read_text().strip() == "941"
    assert (tmp_path / ".last_fetched_date").read_text().strip() == "2026-11-07"
    fetch._write_fetched_state("942", "unparseable")
    assert not (tmp_path / ".last_fetched_date").exists()
    assert not list(tmp_path.glob("*.tmp"))
