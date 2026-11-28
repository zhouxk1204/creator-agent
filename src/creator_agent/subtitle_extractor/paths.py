"""Where tasks land: the doraemon materials folder recorded by the launchers.

``logs/.last_doraemon_materials`` is written on every materials download and
holds the desktop folder for the current batch, so subtitle tasks go next to the
source videos instead of a throwaway ``output/`` directory.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MATERIALS_MARKER = Path("logs") / ".last_doraemon_materials"


def _read_marker(path: Path) -> Path | None:
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    if not text:
        return None
    folder = Path(text)
    return folder if folder.is_dir() else None


def materials_root(start: Path | None = None) -> Path | None:
    """Materials folder from the marker file, searched from ``start`` upwards."""
    here = (start or Path.cwd()).resolve()
    for base in [here, *here.parents]:
        marker = base / MATERIALS_MARKER
        if marker.is_file():
            folder = _read_marker(marker)
            if folder is not None:
                return folder
    return None


def default_output_root(start: Path | None = None) -> Path:
    """Root for new task folders: the materials folder when recorded, else ``output/``."""
    folder = materials_root(start)
    return folder if folder is not None else Path.cwd() / "output"


def default_task_dir(video_stem: str, start: Path | None = None) -> Path:
    return default_output_root(start) / f"{video_stem}_srt"
