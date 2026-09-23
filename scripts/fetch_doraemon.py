"""Fetch a Doraemon episode page from TV Asahi: titles, synopses, images.

Takes an episode number (e.g. ``934`` or ``0934``), builds the URL
``https://www.tv-asahi.co.jp/doraemon/story/{episode:04d}/``, and saves:

    storage/doraemon/{episode:04d}/
    ├── metadata.json   # episode, url, broadcast_date, stories[{title, synopsis, image, copyright}]
    ├── story.md        # human-readable titles + synopses (Markdown)
    └── story_1.jpg, story_2.jpg, ...

Usage:
    uv run python scripts/fetch_doraemon.py 934
"""

from __future__ import annotations

import html as html_mod
import json
import re
import sys
from pathlib import Path

import httpx

BASE_URL = "https://www.tv-asahi.co.jp/doraemon/story/{ep}/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
OUT_ROOT = Path(__file__).resolve().parent.parent / "storage" / "doraemon"

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
        r'<h2 class="story-title">(.*?)</h2>\s*<div class="read-box">(.*?)(?=<h2 class="story-title">|<!-- /?\.?read-box|</section>)',
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
