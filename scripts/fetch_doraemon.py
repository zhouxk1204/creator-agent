"""Fetch a Doraemon episode page from TV Asahi: titles, synopses, images.

Takes an episode number (e.g. ``934`` or ``0934``), builds the URL
``https://www.tv-asahi.co.jp/doraemon/story/{episode:04d}/``, and saves:

    storage/doraemon/{episode:04d}/
    ├── metadata.json   # episode, url, broadcast_date, stories[{title, synopsis, image, copyright}]
    ├── story.md        # human-readable titles + synopses (Markdown)
    └── story_1.jpg, story_2.jpg, ...

Plus, split per story (e.g. episode 935 → stories 935_1, 935_2):
    <vault>/简介/{ep}/{ep}_{i}.md    # '# <title>' + '![[封面/{ep}/...]]' +
                                    # synopsis body, for `creator-agent learn`
    <vault>/封面/{ep}/{ep}_{i}_4k.jpg  # cover mirrored into the vault so the
                                      # 简介 note can embed it (Obsidian wikilink)
    ~/Desktop/{ep}_{i}_4k.jpg  # cover upscaled by executing the user's ComfyUI
                               # workflow (doraemonn_cover.json); a headless
                               # server is auto-started when none is running.
                               # Plain {ep}_{i}.jpg copy as fallback.

Usage:
    uv run python scripts/fetch_doraemon.py 934
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE_URL = "https://www.tv-asahi.co.jp/doraemon/story/{ep}/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
OUT_ROOT = Path(__file__).resolve().parent.parent / "storage" / "doraemon"
# Per-story outputs: synopsis notes and covers go to the Obsidian vault
# (notes are consumed by `creator-agent learn`, see translate/learn.py);
# covers are ALSO copied to the Desktop for the user's publishing flow.
VAULT_SYNOPSIS_DIR = Path(r"C:\Users\34696\Documents\Obsidian Vault\doraemon_subtitle\简介")
VAULT_COVERS_DIR = Path(r"C:\Users\34696\Documents\Obsidian Vault\doraemon_subtitle\封面")
DESKTOP_DIR = Path.home() / "Desktop"
# ComfyUI upscales covers to "4K" by executing the user's own workflow file
# (COMFYUI_WORKFLOW, UI format -> converted to an API prompt here). Discovery
# order: $COMFYUI_URL if set, then a running Desktop app (:8000), then a
# manually started server (:8188); if none answers we boot our own headless
# server from the cloned core (COMFYUI_CORE) with the Desktop venv's python
# and shut it down again at the end.
COMFYUI_WORKFLOW = Path(
    r"C:\Users\34696\Documents\ComfyUI\user\default\workflows\doraemonn_cover.json"
)
COMFYUI_CORE = Path(r"C:\agents\ComfyUI")  # git clone of comfyanonymous/ComfyUI
COMFYUI_VENV_PY = Path(r"C:\Users\34696\Documents\ComfyUI\.venv\Scripts\python.exe")
COMFYUI_BASE = Path(r"C:\Users\34696\Documents\ComfyUI")
COMFYUI_EXTRA_MODELS = Path(os.environ.get("APPDATA", "")) / "ComfyUI" / "extra_models_config.yaml"
COMFYUI_OWN_URL = "http://127.0.0.1:8188"
COMFYUI_CANDIDATE_URLS = ["http://127.0.0.1:8000", COMFYUI_OWN_URL]  # 8000 = Desktop app

# Make non-ASCII print correctly on the Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _clean_text(fragment: str) -> str:
    """Strip tags, turn <br> into newlines, unescape entities, tidy whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def parse_page(page: str) -> dict:
    date_m = re.search(r'<p class="date">([^<]+)</p>', page)
    h1_m = re.search(r"<h1>(.*?)</h1>", page, re.S)

    stories = []
    # Each story: <h2 class="story-title">...</h2><div class="read-box">...</div>
    blocks = re.findall(
        r'<h2 class="story-title">(.*?)</h2>\s*<div class="read-box">(.*?)'
        r'(?=<h2 class="story-title">|<!-- /?\.?read-box|</section>)',
        page,
        re.S,
    )
    for title_html, body in blocks:
        img_m = re.search(r'<p class="story-img">\s*<img src="([^"]+)"', body)
        txt_m = re.search(r'<p class="txt_idt">(.*?)</p>', body, re.S)
        cpr_m = re.search(r'<p class="copyright">(.*?)</p>', body, re.S)
        stories.append(
            {
                "title": _clean_text(title_html),
                "synopsis": _clean_text(txt_m.group(1)) if txt_m else "",
                "image": img_m.group(1) if img_m else "",
                "copyright": _clean_text(cpr_m.group(1)) if cpr_m else "",
            }
        )

    return {
        "broadcast_date": _clean_text(date_m.group(1)) if date_m else "",
        "episode_title": _clean_text(h1_m.group(1)) if h1_m else "",
        "stories": stories,
    }


# Widget name per node type, in widgets_values order, for UI->API conversion.
# Add an entry here if doraemonn_cover.json gains a node type with widgets.
_UI_WIDGET_INPUTS = {
    "UpscaleModelLoader": ["model_name"],
    "LoadImage": ["image"],  # 2nd widget ('upload') is UI-only, not an API input
}


def ui_to_api_prompt(wf: dict, image_name: str) -> dict:
    """Convert a UI-format ComfyUI workflow to an API prompt.

    Links are resolved through the workflow's links array; widget values are
    mapped to input names via _UI_WIDGET_INPUTS. The LoadImage node's image is
    replaced with image_name (the file we uploaded). Raises ValueError on
    unsupported node types so a silently wrong prompt is never submitted.
    """
    # links array: [id, src node, src slot, dst node, dst slot, type]
    links = {link[0]: (str(link[1]), link[2]) for link in wf.get("links", [])}
    prompt = {}
    for node in wf.get("nodes", []):
        if node.get("mode", 0) in (2, 4):  # muted / bypassed
            continue
        ntype = node["type"]
        widgets = node.get("widgets_values") or []
        names = _UI_WIDGET_INPUTS.get(ntype)
        if names is None and widgets:
            raise ValueError(
                f"workflow node type {ntype!r} has widgets but no entry in "
                "_UI_WIDGET_INPUTS; add it in fetch_doraemon.py"
            )
        inputs = {name: val for name, val in zip(names or [], widgets)}
        for inp in node.get("inputs", []):
            if inp.get("link") is not None:
                inputs[inp["name"]] = list(links[inp["link"]])
        if ntype == "LoadImage":
            inputs["image"] = image_name
        prompt[str(node["id"])] = {"class_type": ntype, "inputs": inputs}
    if not prompt:
        raise ValueError("workflow contains no executable nodes")
    return prompt


def _server_up(client: httpx.Client, url: str) -> bool:
    try:
        return client.get(f"{url}/system_stats", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_comfyui(client: httpx.Client, boot_timeout: int = 240) -> tuple[str | None, subprocess.Popen | None]:
    """Find a running ComfyUI server, or boot a headless one from COMFYUI_CORE.

    Returns (base_url, proc); proc is None when the server was already running
    (caller must NOT terminate it) or when no server could be started (then
    base_url is None too and the caller falls back to plain copies).
    """
    candidates = [os.environ["COMFYUI_URL"]] if os.environ.get("COMFYUI_URL") else COMFYUI_CANDIDATE_URLS
    for url in candidates:
        if _server_up(client, url):
            return url, None

    if not (COMFYUI_CORE / "main.py").is_file() or not COMFYUI_VENV_PY.is_file():
        print("  comfyui   : no server running and cloned core/venv not found")
        return None, None
    print("  comfyui   : no server running - starting headless ComfyUI (may take ~30-60s)...")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = OUT_ROOT / "comfyui_server.log"
    cmd = [
        str(COMFYUI_VENV_PY),
        str(COMFYUI_CORE / "main.py"),
        "--base-directory", str(COMFYUI_BASE),  # models/, custom_nodes/ etc. live here
        "--user-directory", str(COMFYUI_BASE / "user"),
        "--input-directory", str(COMFYUI_BASE / "input"),
        "--output-directory", str(COMFYUI_BASE / "output"),
        "--extra-model-paths-config", str(COMFYUI_EXTRA_MODELS),
        "--disable-auto-launch",
        "--disable-all-custom-nodes",
        "--listen", "127.0.0.1",
        "--port", COMFYUI_OWN_URL.rsplit(":", 1)[1],
    ]
    with log_path.open("wb") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(COMFYUI_CORE))
        deadline = time.monotonic() + boot_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                print(f"  comfyui   : server exited early (code {proc.returncode}) - see {log_path}")
                return None, None
            if _server_up(client, COMFYUI_OWN_URL):
                return COMFYUI_OWN_URL, proc
            time.sleep(2)
    proc.terminate()
    print(f"  comfyui   : server did not come up in {boot_timeout}s - see {log_path}")
    return None, None


def comfy_upscale(client: httpx.Client, base_url: str, img_path: Path, dest: Path, timeout: int = 300) -> bool:
    """Run img_path through the user's doraemonn_cover workflow -> dest.

    Returns False when the run fails/times out, so the caller can fall back
    to a plain copy.
    """
    try:
        up = client.post(
            f"{base_url}/upload/image",
            files={"image": (img_path.name, img_path.read_bytes(), "image/jpeg")},
            data={"overwrite": "true"},
        )
        up.raise_for_status()
        uploaded = up.json()["name"]

        wf = json.loads(COMFYUI_WORKFLOW.read_text(encoding="utf-8"))
        resp = client.post(f"{base_url}/prompt", json={"prompt": ui_to_api_prompt(wf, uploaded)})
        resp.raise_for_status()
        pid = resp.json()["prompt_id"]

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(1)
            hist = client.get(f"{base_url}/history/{pid}").json()
            if pid not in hist:
                continue
            for node_out in hist[pid].get("outputs", {}).values():
                for img in node_out.get("images", []):
                    view = client.get(
                        f"{base_url}/view",
                        params={
                            "filename": img["filename"],
                            "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "temp"),
                        },
                    )
                    view.raise_for_status()
                    dest.write_bytes(view.content)
                    return True
            return False  # finished but produced no image
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        print(f"  cover(4k) : upscale failed ({exc})")
        return False
    return False  # timed out


def main(episode: str) -> None:
    if not episode.strip().isdigit():
        print(f"Invalid episode number: {episode!r} (expected e.g. 934)")
        sys.exit(2)
    ep = f"{int(episode):04d}"
    url = BASE_URL.format(ep=ep)

    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"HTTP {resp.status_code} for {url} — episode may not exist.")
            sys.exit(1)
        data = parse_page(resp.text)

        if not data["stories"]:
            print(f"No stories parsed from {url} — page layout may have changed.")
            sys.exit(1)

        out_dir = OUT_ROOT / ep
        out_dir.mkdir(parents=True, exist_ok=True)

        data["episode"] = ep
        data["url"] = url

        for i, story in enumerate(data["stories"], 1):
            src = story["image"]
            if not src:
                continue
            img_url = src if src.startswith("http") else f"https://www.tv-asahi.co.jp{src}"
            ext = Path(img_url.split("?")[0]).suffix or ".jpg"
            img_path = out_dir / f"story_{i}{ext}"
            img_path.write_bytes(client.get(img_url).content)
            story["image_file"] = img_path.name
            print(f"  image     : {img_path}")

        (out_dir / "metadata.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        lines = [
            f"# 第{ep}话 {data['episode_title']}",
            "",
            f"- 放送日：{data['broadcast_date']}",
            f"- 来源：{url}",
            "",
        ]
        for i, story in enumerate(data["stories"], 1):
            lines += [f"## {i}. {story['title']}", ""]
            if story.get("image_file"):
                lines += [f"![{story['title']}]({story['image_file']})", ""]
            # Blank line between paragraphs so Markdown renders them separately.
            for para in story["synopsis"].splitlines():
                lines += [para, ""]
            if story["copyright"]:
                lines += [f"> {story['copyright']}", ""]
        (out_dir / "story.md").write_text("\n".join(lines), encoding="utf-8")

        # Split per-story: <ep>_<i>.md synopsis notes under 简介/<ep>/ (named
        # to match episodes/<ep>_<i>/ dirs, e.g. 935_1.md), covers to Desktop
        # + 封面/<ep>/ so the note can embed them via an Obsidian wikilink.
        ep_short = str(int(episode))
        note_dir = VAULT_SYNOPSIS_DIR / ep_short
        cover_dir = VAULT_COVERS_DIR / ep_short
        note_dir.mkdir(parents=True, exist_ok=True)
        cover_dir.mkdir(parents=True, exist_ok=True)
        comfy_url, comfy_proc = ensure_comfyui(client)
        try:
            for i, story in enumerate(data["stories"], 1):
                cover_name: str | None = None
                if story.get("image_file"):
                    src_img = out_dir / story["image_file"]
                    dst_4k = DESKTOP_DIR / f"{ep_short}_{i}_4k{src_img.suffix}"
                    if comfy_url and comfy_upscale(client, comfy_url, src_img, dst_4k):
                        print(f"  cover(4k) : {dst_4k}")
                        cover_name = dst_4k.name
                    else:
                        if not comfy_url:
                            print("  cover(4k) : ComfyUI unavailable - copying original instead")
                        dst_img = DESKTOP_DIR / f"{ep_short}_{i}{src_img.suffix}"
                        shutil.copy2(src_img, dst_img)
                        print(f"  cover     : {dst_img}")
                        cover_name = dst_img.name
                    # Mirror into the vault so 简介 notes can embed the cover.
                    shutil.copy2(DESKTOP_DIR / cover_name, cover_dir / cover_name)
                    print(f"  vault     : {cover_dir / cover_name}")

                # '# <title>' heading + cover embed + synopsis body (see
                # learn.parse_synopsis_md); strip the site's 「」 brackets so
                # the heading is the bare title.
                note_lines = [f"# {story['title'].strip('「」')}", ""]
                if cover_name:
                    note_lines += [f"![[封面/{ep_short}/{cover_name}]]", ""]
                note_lines += story["synopsis"].splitlines()
                note_path = note_dir / f"{ep_short}_{i}.md"
                note_path.write_text("\n".join(note_lines).strip() + "\n", encoding="utf-8")
                print(f"  synopsis  : {note_path}")
        finally:
            if comfy_proc is not None:  # only stop the server WE started
                comfy_proc.terminate()

    print(f"\n第{ep}话 {data['episode_title']}")
    print(f"  broadcast : {data['broadcast_date']}")
    for story in data["stories"]:
        print(f"  story     : {story['title']}  ({len(story['synopsis'])} chars)")
    print(f"\nSaved to: {out_dir.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: fetch_doraemon.py <episode_number>  (e.g. 934)")
        sys.exit(2)
    main(sys.argv[1])
