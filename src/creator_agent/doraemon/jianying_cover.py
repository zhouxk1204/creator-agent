"""Render the 3:4 Douyin portrait cover with 剪映 (Windows + GUI automation).

Why 剪映 instead of drawing with Pillow: the template's title line is a 剪映
"花字" (layered gradient + 3D extrusion) that cannot be reproduced faithfully by
a text renderer. Driving the real editor is slower but pixel-identical to the
template, so the cover always matches the video's look.

How it works — 剪映 11.x encrypts ``draft_content.json``, and 11.7 also dropped
the ``decrypt``/``encrypt`` exports from ``videoeditor.dll`` that every existing
decryption tool relied on. A plaintext draft is still accepted by the app, so
this module never touches the encrypted files: it builds a **new** draft from
``assets/doraemon_cover_template`` (plaintext timeline + skeleton) and lets 剪映
render it.

Requirements: Windows, 剪映专业版 11.7, and the ``covers`` optional extra
(``uv sync --extra covers``). The GUI layer is ``jianying-tool``: 剪映 is a
Qt/QML app with no UI Automation tree, so every click is a screenshot match.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEMPLATE_DIR = REPO_ROOT / "assets" / "doraemon_cover_template"

TIMELINE_ID = "5F8328CF-2F14-481d-B50B-893715572C55"
PHOTO_ID = "87EA8BCA-E035-423b-B2BC-8FB4B65B04A9"  # story_1.jpg → 用户图片
CARD_HASH = "53413954415a9c35ee3c9bbf3e057aca"  # 绿色卡片层
FRAME_HASH = "584b9926223e8cb97de411e272b2a443"  # 黑底白圆角边框层
TEXT_EPISODE_OLD = "941"  # 下面那行
TEXT_TITLE_OLD = "定时爆炸贴纸"  # 上面那行大号花字
SOURCE_W, SOURCE_H = 1920, 1080
TV_ASAHI_BASE = "https://www.tv-asahi.co.jp/doraemon/story"


class CoverRenderError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


# ----------------------------------------------------------------- paths
def template_dir() -> Path:
    configured = os.environ.get("DORAEMON_COVER_TEMPLATE")
    path = Path(configured).expanduser() if configured else DEFAULT_TEMPLATE_DIR
    if not (path / "template.json").is_file():
        raise CoverRenderError(f"封面模板缺失：{path}/template.json（设置 DORAEMON_COVER_TEMPLATE 指向模板目录）")
    return path


def draft_root() -> Path:
    configured = os.environ.get("JIANYING_DRAFT_ROOT")
    if configured:
        return Path(configured).expanduser()
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def jianying_exe() -> Path:
    configured = os.environ.get("JIANYING_EXE")
    if configured:
        exe = Path(configured).expanduser()
        if not exe.is_file():
            raise CoverRenderError(f"JIANYING_EXE 不存在：{exe}")
        return exe
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    candidates = [Path(p) for p in glob.glob(str(local / "JianyingPro" / "Apps" / "*" / "JianyingPro.exe"))]
    if not candidates:
        raise CoverRenderError("找不到剪映安装目录（可用 JIANYING_EXE 指定 JianyingPro.exe）")
    return sorted(candidates, key=lambda p: p.parent.name)[-1]


def requirements_problem() -> str | None:
    """Human-readable reason the renderer cannot run here, or ``None``."""
    if os.name != "nt":
        return "剪映封面生成只在 Windows 上可用"
    try:
        jianying_exe()
    except CoverRenderError as exc:
        return str(exc)
    try:
        import jianying_tool.control  # noqa: F401
    except ModuleNotFoundError:
        return '缺少 GUI 依赖：先运行 "uv sync --extra covers"'
    except Exception as exc:  # noqa: BLE001
        return f"GUI 依赖加载失败：{exc}"
    return None


# ----------------------------------------------------------------- image prep
def prepare_photo(src: Path, dst: Path, *, fit: str = "cover") -> tuple[int, int]:
    from PIL import Image

    image = Image.open(src).convert("RGB")
    target_ratio = SOURCE_W / SOURCE_H
    width, height = image.size
    if fit == "cover":
        ratio = width / height
        if ratio > target_ratio:
            new_width = int(round(height * target_ratio))
            left = (width - new_width) // 2
            image = image.crop((left, 0, left + new_width, height))
        elif ratio < target_ratio:
            new_height = int(round(width / target_ratio))
            top = (height - new_height) // 2
            image = image.crop((0, top, width, top + new_height))
        image = image.resize((SOURCE_W, SOURCE_H), Image.LANCZOS)
    else:
        image.thumbnail((SOURCE_W, SOURCE_H), Image.LANCZOS)
        canvas = Image.new("RGB", (SOURCE_W, SOURCE_H), (0, 0, 0))
        canvas.paste(image, ((SOURCE_W - image.width) // 2, (SOURCE_H - image.height) // 2))
        image = canvas
    dst.parent.mkdir(parents=True, exist_ok=True)
    image.save(dst, quality=95)
    return image.size


# ----------------------------------------------------------------- draft build
def _set_text(material: dict, new_text: str) -> None:
    parsed = json.loads(material["content"])
    payload = parsed[0] if isinstance(parsed, list) else parsed
    payload["text"] = new_text
    for style in payload.get("styles", []):
        if "range" in style:
            style["range"] = [0, len(new_text)]
    material["content"] = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def build_draft(*, name: str, image: Path, episode: str, title: str, workdir: Path) -> Path:
    template = template_dir()
    draft = draft_root() / name
    if draft.exists():
        shutil.rmtree(draft)
    shutil.copytree(template / "skeleton", draft)

    media = draft / "media"
    media.mkdir(parents=True, exist_ok=True)
    photo = media / "photo.jpg"
    prepare_photo(image, photo)
    card = media / "card_green.jpg"
    frame = media / "frame_black.jpg"
    shutil.copy2(template / "layers" / "card_green.jpg", card)
    shutil.copy2(template / "layers" / "frame_black.jpg", frame)

    data = json.loads((template / "template.json").read_text(encoding="utf-8-sig"))
    for material in data["materials"]["videos"]:
        path = str(material.get("path") or "")
        if material.get("id") == PHOTO_ID:
            target, filename = photo, "photo.jpg"
        elif path.endswith(f"{CARD_HASH}.jpg"):
            target, filename = card, card.name
        elif path.endswith(f"{FRAME_HASH}.jpg"):
            target, filename = frame, frame.name
        else:
            raise CoverRenderError(f"模板里有未识别的视频素材：{material.get('id')}")
        material["path"] = str(target)
        material["material_name"] = filename
        if target is photo:
            from PIL import Image

            with Image.open(target) as handle:
                material["width"], material["height"] = handle.size

    for material in data["materials"]["texts"]:
        parsed = json.loads(material["content"])
        payload = parsed[0] if isinstance(parsed, list) else parsed
        if payload["text"] == TEXT_EPISODE_OLD:
            _set_text(material, episode)
        elif payload["text"] == TEXT_TITLE_OLD:
            _set_text(material, title)

    plain = json.dumps(data, ensure_ascii=False, indent=2)
    (draft / "draft_content.json").write_text(plain, encoding="utf-8")
    timeline = draft / "Timelines" / TIMELINE_ID
    if timeline.is_dir():
        (timeline / "draft_content.json").write_text(plain, encoding="utf-8")

    store_path = draft / "draft_virtual_store.json"
    store = json.loads(store_path.read_text(encoding="utf-8-sig"))
    for group in store.get("draft_virtual_store", []):
        for item in group.get("value", []):
            if str(item.get("path", "")).endswith("draft_content.json"):
                item["is_encrypted"] = False
    store_path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return draft


# ----------------------------------------------------------------- 剪映 driving
def _running() -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/NH"], capture_output=True, text=True, errors="ignore"
    ).stdout
    return "JianyingPro.exe" in out


def stop_jianying() -> None:
    if not _running():
        return
    subprocess.run(["taskkill", "/F", "/IM", "JianyingPro.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "JianyingProTray.exe"], capture_output=True)
    for _ in range(40):
        if not _running():
            return
        time.sleep(0.5)


def _wait_for_home(timeout: float = 150.0) -> None:
    from jianying_tool.control import capture as capture_mod
    from jianying_tool.control import pages, window

    deadline = time.time() + timeout
    last = "no window"
    while time.time() < deadline:
        try:
            info = window.resolve()
            verdict = pages.identify(capture_mod.grab(info.hwnd).image)
            if verdict.page is pages.Page.HOME:
                return
            last = verdict.page.value
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
        time.sleep(1.5)
    raise CoverRenderError(f"剪映没有进入首页（最后状态：{last}）")


def _export(draft_name: str, workdir: Path, title: str) -> Path:
    from jianying_tool.control import api as control_api

    result = control_api.export_draft(
        draft_name,
        output_dir=workdir,
        title=title,
        resolution="原始",
        format="MP4",
        close=True,
        timeout=900.0,
        log=lambda message: log(f"   [剪映] {message}"),
    )
    output = result.get("output")
    if not output:
        raise CoverRenderError("剪映导出结束但没有产物")
    return Path(output)


def _open_and_export(draft_name: str, workdir: Path, title: str, attempts: int = 3) -> Path:
    """剪映 scans the draft folder at startup; retry when it opens the wrong card."""
    exe = jianying_exe()
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        stop_jianying()
        subprocess.Popen([str(exe)], close_fds=True)
        _wait_for_home()
        time.sleep(5.0)
        try:
            return _export(draft_name, workdir, title)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "different draft" not in message and "no editor appeared" not in message:
                raise
            last = exc
            log(f"   打开的不是目标草稿（第 {attempt} 次），重试")
    raise CoverRenderError(f"多次重试仍未能打开草稿 {draft_name}: {last}")


def prune_index() -> None:
    """Drop draft-index entries whose folder is gone (剪映 shows them as broken)."""
    index = draft_root() / "root_meta_info.json"
    if not index.is_file():
        return
    try:
        data = json.loads(index.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return
    store = data.get("all_draft_store")
    if not isinstance(store, list):
        return
    kept = [entry for entry in store if Path(str(entry.get("draft_fold_path", ""))).is_dir()]
    if len(kept) == len(store):
        return
    data["all_draft_store"] = kept
    ids = {entry.get("draft_id") for entry in kept}
    if isinstance(data.get("draft_ids"), list):
        data["draft_ids"] = [item for item in data["draft_ids"] if item in ids]
    index.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _ffmpeg() -> str:
    bundled = jianying_exe().parent / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _first_frame(video: Path, out: Path) -> Path:
    subprocess.run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vf",
            r"select=eq(n\,0)",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    if not out.is_file() or out.stat().st_size == 0:
        raise CoverRenderError(f"抽帧失败：{out}")
    return out


def safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text).strip() or "cover"


def render_portrait_cover(*, image: Path, episode: str, title: str, out: Path) -> Path:
    """Render one 1440×1920 cover and write it to ``out``."""
    problem = requirements_problem()
    if problem:
        raise CoverRenderError(problem)
    image = Path(image)
    if not image.is_file():
        raise CoverRenderError(f"图片不存在：{image}")

    stamp = time.strftime("%H%M%S")
    draft_name = f"CV_{safe_name(str(episode))}_{stamp}"
    export_title = f"{safe_name(str(episode))}_{safe_name(title)}_{stamp}"

    with tempfile.TemporaryDirectory(prefix="doraemon_cover_") as temp:
        workdir = Path(temp)
        stop_jianying()
        draft = build_draft(name=draft_name, image=image, episode=str(episode), title=title, workdir=workdir)
        try:
            log(f"   草稿已生成 {draft.name}")
            video = _open_and_export(draft_name, workdir, export_title)
            frame = _first_frame(video, workdir / f"{export_title}.png")
            out.parent.mkdir(parents=True, exist_ok=True)
            from PIL import Image as PillowImage

            with PillowImage.open(frame) as handle:
                handle.convert("RGB").save(out, quality=95, subsampling=0)
            return out
        finally:
            stop_jianying()
            shutil.rmtree(draft, ignore_errors=True)
            prune_index()
