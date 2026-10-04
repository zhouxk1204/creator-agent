"""Knowledge store for learned subtitle-translation corrections.

Layout under ``<project_dir>/knowledge/``:

- ``error_cases.json``       — categorized AI-vs-human translation errors
- ``terminology.json``       — fixed JA -> ZH terms (names, jargon)
- ``segmentation_rules.json``— structure/timeline findings (split/merge/...)
- ``translation_rules.md``   — human-readable rule bullets (editable)
- ``terminology.md``         — Obsidian-friendly glossary table (regenerated)
- ``error_cases.md``         — Obsidian-friendly cases grouped by category,
                               linked to each episode's 05_summary.md
- ``translation_memory.json``— compiled bundle the translator injects into
                               prompts (regenerated on every learn run)

All updates are append-with-dedupe, so re-learning the same episode is
idempotent. ``prompt_section`` retrieves what's relevant for one translation
batch: glossary terms that literally appear in the batch, plus the most
similar past error cases (char-bigram overlap), plus the rules.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

ERROR_CASES = "error_cases.json"
TERMINOLOGY = "terminology.json"
SEGMENTATION = "segmentation_rules.json"
RULES_MD = "translation_rules.md"
TERMINOLOGY_MD = "terminology.md"
ERROR_CASES_MD = "error_cases.md"
COMPILED = "translation_memory.json"
EPISODE_SUMMARY = "05_summary.md"  # mirrors learn.SUMMARY_FILE (kept here to avoid a circular import)


def _bigrams(text: str) -> set[str]:
    t = "".join(str(text).split())
    return {t[i : i + 2] for i in range(len(t) - 1)} or {t} if t else set()


class MemoryStore:
    def __init__(self, knowledge_dir: Path) -> None:
        self.dir = Path(knowledge_dir)
        self.cases: list[dict] = []
        self.terms: list[dict] = []
        self.findings: list[dict] = []
        self.rules: list[str] = []

    # -- load / save ----------------------------------------------------------

    @classmethod
    def load(cls, project_dir: str | Path) -> MemoryStore | None:
        """Load existing knowledge; None when nothing has been learned yet."""
        kdir = Path(project_dir) / "knowledge"
        if not kdir.is_dir():
            return None
        store = cls(kdir)
        store.cases = _read_json(kdir / ERROR_CASES).get("cases", [])
        store.terms = _read_json(kdir / TERMINOLOGY).get("terms", [])
        store.findings = _read_json(kdir / SEGMENTATION).get("findings", [])
        rules_file = kdir / RULES_MD
        if rules_file.exists():
            store.rules = [
                ln.lstrip("- ").strip()
                for ln in rules_file.read_text(encoding="utf-8").splitlines()
                if ln.strip().startswith("- ")
            ]
        return store if (store.cases or store.terms or store.rules) else None

    @classmethod
    def load_or_empty(cls, project_dir: str | Path) -> MemoryStore:
        return cls.load(project_dir) or cls(Path(project_dir) / "knowledge")

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.dir / ERROR_CASES, {"cases": self.cases})
        _write_json(self.dir / TERMINOLOGY, {"terms": self.terms})
        _write_json(self.dir / SEGMENTATION, {"findings": self.findings})
        (self.dir / RULES_MD).write_text(
            '# 翻译规则库（learn 自动累积，可手工编辑，每行一条，以 "- " 开头）\n\n'
            + "".join(f"- {r}\n" for r in self.rules),
            encoding="utf-8",
        )
        self._write_obsidian_md()
        self.compile()

    def _write_obsidian_md(self) -> None:
        """Regenerate the Obsidian-facing glossary / case notes."""
        term_rows = "\n".join(
            f"| {_cell(t.get('ja', ''))} | {_cell(t.get('zh', ''))} | {_cell(t.get('note', ''))} |" for t in self.terms
        )
        (self.dir / TERMINOLOGY_MD).write_text(
            "# 术语库（learn 自动累积，翻译时强制统一）\n\n| 日文 | 中文 | 备注 |\n|---|---|---|\n" + term_rows + "\n",
            encoding="utf-8",
        )

        by_cat: dict[str, list[dict]] = {}
        for c in self.cases:
            for cat in c.get("categories", []) or ["未分类"]:
                by_cat.setdefault(cat, []).append(c)
        parts = [
            "# 错误案例库（learn 自动累积）",
            "",
            f"共 {len(self.cases)} 条，按类别分组；剧集列链接到该集的学习总结。",
        ]
        for cat in sorted(by_cat, key=lambda k: -len(by_cat[k])):
            parts += [
                "",
                f"## {cat}（{len(by_cat[cat])}）",
                "",
                "| 剧集 | 日文 | AI | 人工 | 说明 |",
                "|---|---|---|---|---|",
            ]
            for c in by_cat[cat]:
                ep = c.get("episode", "")
                link = f"[{ep}](../episodes/{quote(ep)}/{EPISODE_SUMMARY})" if ep else ""
                parts.append(
                    f"| {link} | {_cell(c.get('ja', ''))} | {_cell(c.get('ai', ''))} "
                    f"| {_cell(c.get('final', ''))} | {_cell(c.get('note', ''))} |"
                )
        (self.dir / ERROR_CASES_MD).write_text("\n".join(parts) + "\n", encoding="utf-8")

    def compile(self) -> None:
        """Regenerate the bundle the translator injects."""
        _write_json(
            self.dir / COMPILED,
            {"glossary": self.terms, "cases": self.cases, "rules": self.rules},
        )

    # -- updates (deduped) ------------------------------------------------------

    def add_cases(self, cases: list[dict]) -> int:
        seen = {(c.get("ja", ""), c.get("ai", "")) for c in self.cases}
        added = 0
        for c in cases:
            key = (c.get("ja", ""), c.get("ai", ""))
            if key not in seen and any(key):
                seen.add(key)
                self.cases.append(c)
                added += 1
        return added

    def add_terms(self, terms: list[dict]) -> int:
        by_ja = {t.get("ja", ""): t for t in self.terms if t.get("ja")}
        added = 0
        for t in terms:
            ja = t.get("ja", "")
            if not ja:
                continue
            if ja in by_ja:
                by_ja[ja].update({k: v for k, v in t.items() if v})  # human correction wins
            else:
                by_ja[ja] = t
                added += 1
        self.terms = list(by_ja.values())
        return added

    def add_findings(self, findings: list[dict]) -> int:
        seen = {(f.get("kind", ""), f.get("ja", ""), f.get("episode", "")) for f in self.findings}
        added = 0
        for f in findings:
            key = (f.get("kind", ""), f.get("ja", ""), f.get("episode", ""))
            if key not in seen:
                seen.add(key)
                self.findings.append(f)
                added += 1
        return added

    def add_rules(self, rules: list[str]) -> int:
        seen = set(self.rules)
        added = 0
        for r in rules:
            r = str(r).strip().lstrip("- ").strip()
            if r and r not in seen:
                seen.add(r)
                self.rules.append(r)
                added += 1
        return added

    # -- retrieval for prompt injection ----------------------------------------

    def prompt_section(self, batch_ja_text: str, max_cases: int = 10) -> str:
        """Memory block prepended to a translation batch's user message."""
        sections: list[str] = []
        hits = [t for t in self.terms if t.get("ja") and t["ja"] in batch_ja_text]
        if hits:
            lines = [f"- {t['ja']} → {t.get('zh', '')}" + (f"（{t['note']}）" if t.get("note") else "") for t in hits]
            sections.append("【术语库：以下译名必须统一使用】\n" + "\n".join(lines))
        if self.cases:
            bg = _bigrams(batch_ja_text)
            scored = sorted(
                self.cases,
                key=lambda c: len(bg & _bigrams(c.get("ja", ""))),
                reverse=True,
            )
            top = [c for c in scored[:max_cases] if bg & _bigrams(c.get("ja", ""))]
            if top:
                lines = [
                    f"- 日: {c.get('ja', '')} ｜ ✗ {c.get('ai', '')} ｜ ✓ {c.get('final', '')}"
                    f"（{','.join(c.get('categories', []))}）"
                    for c in top
                ]
                sections.append("【历史错误案例：避免重蹈覆辙】\n" + "\n".join(lines))
        if self.rules:
            sections.append("【翻译规则】\n" + "\n".join(f"- {r}" for r in self.rules[:15]))
        return "\n\n".join(sections)


def _cell(s: str) -> str:
    """Escape a value for a markdown table cell."""
    return str(s).replace("|", "\\|").replace("\n", " ")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
