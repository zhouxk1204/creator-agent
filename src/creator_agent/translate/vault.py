"""Obsidian vault integration for the translation pipeline.

Two things live here:

- **Episode synopses**: ``<project_dir>/简介/<ep>.md`` notes (written by
  ``scripts/fetch_doraemon.py``) give the translator story context for the
  episode being translated. Lookup is by video stem, tolerant of the stem's
  ``#`` being written as ``_`` in the note name (``936#1`` -> ``936_1.md``).
- **Subtitle export**: generated ``<stem>.srt`` / ``<stem>.zh.srt`` are
  mirrored into ``<project_dir>/<stem>/`` so they can be read/edited in
  Obsidian. Copies only when the source is newer, so hand-edits in the
  vault survive re-exports.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SYNOPSIS_DIR = "简介"  # <project_dir>/简介/<ep>.md

# Synopsis injected into translation prompts is capped — the 9B model does
# better with a short, focused context block.
_MAX_SYNOPSIS_CHARS = 500


def parse_synopsis_md(text: str) -> dict:
    """Parse a 简介 note -> {title, synopsis}.

    Canonical format (written by scripts/fetch_doraemon.py): the first-level
    heading IS the title and the body under it is the synopsis::

        # 変身レプリンター

        学校から帰るなり、…

    The legacy format ('# 标题 xxx' + a '## 简介 xxx' section) is still
    accepted. Tolerant of missing space after '#', a leading 标题/简介 label
    on the heading line, and a multi-line synopsis. A '##' section other than
    简介 ends the synopsis capture.
    """
    title, synopsis_lines = "", []
    section = None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("##"):
            head = s.lstrip("#").strip()
            section = "synopsis" if head.startswith("简介") else None
            rest = head.removeprefix("简介").lstrip(" ：:").strip()
            if section and rest:
                synopsis_lines.append(rest)
        elif s.startswith("#"):
            if not title:
                title = s.lstrip("#").strip().removeprefix("标题").lstrip(" ：:").strip()
            # Body directly under the title is the synopsis (canonical format).
            section = "synopsis"
        elif section == "synopsis" and s and not s.startswith("!["):
            # Image embeds (e.g. the '![[封面/...]]' cover line) are display
            # content, not story text — keep them out of the LLM context.
            synopsis_lines.append(s)
    return {"title": title, "synopsis": "\n".join(synopsis_lines)}


def load_synopsis(project_dir: str | Path, stem: str) -> dict | None:
    """``<project_dir>/简介/<ep>/<stem>.md`` -> {title, synopsis}; None when absent.

    ``#`` in the stem is also tried as ``_`` (note names can't always carry
    the video filename's '#'). New layout groups notes per episode
    (``简介/936/936_1.md``); the legacy flat layout (``简介/936_1.md``) is
    still accepted as a fallback.
    """
    if not project_dir:
        return None
    base = Path(project_dir) / SYNOPSIS_DIR
    for name in dict.fromkeys((stem, stem.replace("#", "_"))):
        candidates = [base / name.split("_")[0] / f"{name}.md", base / f"{name}.md"]
        for path in candidates:
            if not path.is_file():
                continue
            try:
                info = parse_synopsis_md(path.read_text(encoding="utf-8"))
            except OSError:
                return None
            if info["title"] or info["synopsis"]:
                if len(info["synopsis"]) > _MAX_SYNOPSIS_CHARS:
                    info["synopsis"] = info["synopsis"][:_MAX_SYNOPSIS_CHARS] + "…"
                return info
    return None


def _progress(msg: str) -> None:
    print(f"[vault] {msg}", file=sys.stderr, flush=True)
    logger.info(msg)


def export_subtitles(project_dir: str | Path, stem: str, files: list[Path]) -> Path | None:
    """Copy subtitle ``files`` into ``<project_dir>/<stem>/`` (created as
    needed). A file is copied only when the target is missing or older than
    the source, so hand-edits made in the vault are never overwritten.
    Returns the target dir, or None when ``project_dir`` is blank."""
    if not project_dir:
        return None
    proj = Path(project_dir)
    target_dir = proj / stem
    copied: list[str] = []
    for f in files:
        f = Path(f)
        if not f.is_file():
            continue
        try:  # outputs may already live inside the vault (e.g. episodes/)
            if f.resolve().is_relative_to(proj.resolve()):
                continue
        except OSError:
            pass
        target = target_dir / f.name
        if target.exists() and target.stat().st_mtime >= f.stat().st_mtime:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        copied.append(f.name)
    if copied:
        _progress(f"{stem}：字幕已同步到 {target_dir}（{', '.join(copied)}）")
    return target_dir
