"""Candidate manifest: one JSONL file per episode, plus human-label syncing.

The manifest is the single source of truth linking a candidate image back to
its episode / scene / timestamp, its quality-filter outcome, and the human
classification. Two safety rules:

  * Re-running extraction NEVER overwrites a human classification: records
    whose review_status is not "unreviewed" keep their character_id and
    review_status; reviewed records missing from a new extraction are kept
    (and reported) instead of being silently dropped.
  * Label syncing COPIES images into characters/<char>/ (the human does the
    copying); sync only updates the manifest and warns about anything
    inconsistent — it never deletes files or clears confirmed labels.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .naming import parse_candidate_id

REVIEW_UNREVIEWED = "unreviewed"
REVIEW_REVIEWED = "reviewed"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass
class CandidateRecord:
    candidate_id: str
    episode_id: str
    scene_id: str
    timestamp_ms: int
    image_path: str  # relative to the character_library root; "" if not saved
    quality_status: str  # "passed" / "filtered_*" / "decode_failed"
    filter_reason: str = ""
    character_id: str | None = None
    review_status: str = REVIEW_UNREVIEWED

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CandidateRecord:
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in d.items() if k in known})


def manifest_path(library_root: Path, episode_id: str) -> Path:
    return library_root / "manifests" / f"{episode_id}_candidates.jsonl"


def load_manifest(path: Path) -> dict[str, CandidateRecord]:
    """candidate_id -> record. Missing file = empty. Duplicate IDs: last wins."""
    records: dict[str, CandidateRecord] = {}
    if not path.is_file():
        return records
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = CandidateRecord.from_dict(json.loads(line))
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"manifest 第 {lineno} 行解析失败 {path}: {e}") from e
        records[rec.candidate_id] = rec
    return records


def save_manifest(path: Path, records: dict[str, CandidateRecord]) -> None:
    """Write the manifest atomically (temp file + replace), sorted by ID."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    lines = [json.dumps(records[k].to_dict(), ensure_ascii=False) for k in sorted(records)]
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(path)


@dataclass
class MergeReport:
    preserved_reviewed: int = 0  # human labels carried over the re-extraction
    kept_missing: list[str] = field(default_factory=list)  # reviewed IDs absent from new extraction


def merge_extraction(
    old: dict[str, CandidateRecord], new: list[CandidateRecord]
) -> tuple[dict[str, CandidateRecord], MergeReport]:
    """Merge a fresh extraction into the existing manifest.

    New extraction data wins for everything EXCEPT human labels on reviewed
    records. Reviewed records that the new extraction no longer produces are
    kept and reported (never silently dropped).
    """
    report = MergeReport()
    merged: dict[str, CandidateRecord] = {}
    new_ids = set()
    for rec in new:
        new_ids.add(rec.candidate_id)
        prev = old.get(rec.candidate_id)
        if prev is not None and prev.review_status != REVIEW_UNREVIEWED:
            rec.character_id = prev.character_id
            rec.review_status = prev.review_status
            report.preserved_reviewed += 1
        merged[rec.candidate_id] = rec
    for cid, prev in old.items():
        if cid not in new_ids:
            merged[cid] = prev
            if prev.review_status != REVIEW_UNREVIEWED:
                report.kept_missing.append(cid)
    return merged, report


@dataclass
class SyncReport:
    updated: int = 0  # newly classified
    already_ok: int = 0  # file present, label unchanged
    conflicts: list[str] = field(default_factory=list)  # relabel attempts, kept original
    unknown_files: list[str] = field(default_factory=list)  # not parseable / not in manifest
    other_episode: list[str] = field(default_factory=list)  # belongs to another episode
    missing_files: list[str] = field(default_factory=list)  # labeled in manifest, file gone

    @property
    def warnings(self) -> int:
        return len(self.conflicts) + len(self.unknown_files) + len(self.other_episode) + len(self.missing_files)


def scan_character_files(library_root: Path) -> dict[str, list[Path]]:
    """candidate_id -> files found under characters/<char>/. A candidate
    copied into TWO character dirs maps to both (conflict, reported later)."""
    characters_dir = library_root / "characters"
    found: dict[str, list[Path]] = {}
    if not characters_dir.is_dir():
        return found
    for char_dir in sorted(p for p in characters_dir.iterdir() if p.is_dir()):
        for f in sorted(char_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES:
                found.setdefault(f.stem, []).append(f)
    return found


def sync_labels(
    library_root: Path, episode_id: str, records: dict[str, CandidateRecord]
) -> tuple[dict[str, CandidateRecord], SyncReport]:
    """Apply human classifications (files copied into characters/<char>/)
    to this episode's manifest. Never clears an existing confirmed label."""
    report = SyncReport()
    found = scan_character_files(library_root)

    for stem, files in found.items():
        cid = parse_candidate_id(stem)
        if cid is None:
            report.unknown_files.extend(str(f) for f in files)
            continue
        if cid.episode_id != episode_id:
            report.other_episode.extend(str(f) for f in files)
            continue
        rec = records.get(stem)
        if rec is None:
            report.unknown_files.extend(str(f) for f in files)
            continue
        char_names = sorted({f.parent.name for f in files})
        if len(char_names) > 1:
            report.conflicts.append(f"{stem}: 同时出现在 {', '.join(char_names)}，保留原标签")
            continue
        new_char = char_names[0]
        if rec.review_status != REVIEW_UNREVIEWED and rec.character_id != new_char:
            report.conflicts.append(f"{stem}: 已确认={rec.character_id}，文件夹={new_char}，保留已确认结果")
            continue
        if rec.review_status != REVIEW_UNREVIEWED:
            report.already_ok += 1
        else:
            rec.character_id = new_char
            rec.review_status = REVIEW_REVIEWED
            report.updated += 1

    # Reviewed records whose image is no longer in the recorded character dir:
    # warn only, never clear the label.
    for cid, rec in records.items():
        if rec.review_status == REVIEW_UNREVIEWED or not rec.character_id:
            continue
        if cid not in found:
            report.missing_files.append(f"{cid} (标签={rec.character_id}，文件不在 characters/ 中)")
    return records, report
