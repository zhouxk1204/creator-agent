"""Learn from human-corrected subtitles (二次学习).

Per episode directory under ``<project_dir>/episodes/<ep>/``:

    01_original_ja.srt   Qwen3-ASR Japanese output
    02_ai_zh.srt         our AI translation
    03_final_zh.srt      human-corrected final
    04_analysis.json     (written by this tool)

Pipeline: align 02 vs 03 by time span (code) -> batches of changed blocks +
the JA original go to the local LLM for error classification (the user's
taxonomy) -> per-episode ``04_analysis.json`` -> merge into
``knowledge/`` (error cases / terminology / segmentation findings / rules)
-> reports (CSV + HTML). Re-running is idempotent (store updates dedupe).
"""

from __future__ import annotations

import csv
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from creator_agent.asr.subtitle import parse_srt
from creator_agent.translate.alignment import align_cues, join_text
from creator_agent.translate.llm import chat
from creator_agent.translate.memory import MemoryStore

if TYPE_CHECKING:
    from creator_agent.config import TranslateSettings

logger = logging.getLogger(__name__)

JA_FILE, AI_FILE, FINAL_FILE, ANALYSIS_FILE = (
    "01_original_ja.srt",
    "02_ai_zh.srt",
    "03_final_zh.srt",
    "04_analysis.json",
)

CLASSIFY_SYSTEM = """你是日中字幕翻译审校专家。给你若干条「日文原文 / AI译文 / 人工修改后译文」，
分析 AI 译文相对于人工译文的差异并分类。

翻译错误类别：词义错误、漏译、多译、语气错误、人称错误、上下文错误、专有名词错误、口语表达不自然、直译。
字幕结构类别：分段错误、过长、过短、断句位置错误、标点问题、阅读速度问题。
时间轴类别：开始时间修改、结束时间修改、合并/拆分字幕（这类差异输入里已用标记给出，如实归类即可）。

同时提取：
- 术语/人名：人工译文中修正或固定的「日文→中文」对照
- 翻译规则：从这批差异中可泛化的经验（一句话一条，面向下次翻译）

只输出一个 JSON 对象，不要任何解释：
{"cases": [{"id": 编号, "categories": [类别...], "note": "一句话说明", "term_ja": "可选", "term_zh": "可选"}],
 "rules": ["规则1", "规则2"]}
AI 译文与人工译文一致的条目不要放进 cases。"""

_KIND_LABEL = {"time_changed": "时间轴有修改", "split": "AI 1 条被拆成多条", "merge": "AI 多条被合并成 1 条"}


def _progress(msg: str) -> None:
    print(f"[learn] {msg}", file=sys.stderr, flush=True)
    logger.info(msg)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def build_classify_prompt(items: list[dict]) -> str:
    """items: [{id, ja, ai, final, flag}] — one classify batch."""
    lines = ["请审校以下条目：", ""]
    for it in items:
        flag = f"（{it['flag']}）" if it.get("flag") else ""
        lines.append(f"#{it['id']}{flag}\n日文: {it['ja']}\nAI: {it['ai']}\n人工: {it['final']}\n")
    return "\n".join(lines)


def parse_learning_json(reply: str) -> dict:
    """Extract the JSON object from a model reply (tolerates prose around it)."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in reply: {reply[:120]!r}")
    data = json.loads(reply[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("reply JSON is not an object")
    data.setdefault("cases", [])
    data.setdefault("rules", [])
    return data


# ---------------------------------------------------------------------------
# per-episode analysis
# ---------------------------------------------------------------------------


def analyze_episode(ep_dir: Path, settings: TranslateSettings, batch_size: int = 20) -> dict:
    """Analyze one episode dir -> the 04_analysis.json payload (also written
    to disk). Raises RuntimeError when required files are missing/empty."""
    ep_dir = Path(ep_dir)
    ja_path, ai_path, final_path = (ep_dir / f for f in (JA_FILE, AI_FILE, FINAL_FILE))
    for p in (ja_path, ai_path, final_path):
        if not p.exists():
            raise RuntimeError(f"missing {p.name} in {ep_dir}")
    ja, ai, final = parse_srt(ja_path), parse_srt(ai_path), parse_srt(final_path)
    if not ai or not final:
        raise RuntimeError(f"empty subtitle in {ep_dir}")
    if len(ja) != len(ai):
        logger.warning("%s: JA cues (%d) != AI cues (%d); JA context may be off", ep_dir.name, len(ja), len(ai))

    alignments = align_cues(ai, final)
    items: list[dict] = []  # for the LLM: text-changed blocks
    findings: list[dict] = []  # structure/timeline, no LLM needed
    records: list[dict] = []  # full per-block detail for 04_analysis.json

    for al in alignments:
        ja_text = join_text(ja, al.ai) if al.ai and al.ai[-1] < len(ja) else ""
        ai_text, final_text = join_text(ai, al.ai), join_text(final, al.final)
        text_changed = ai_text != final_text
        rec = {
            "ai_indices": al.ai,
            "final_indices": al.final,
            "kind": al.kind,
            "ja": ja_text,
            "ai": ai_text,
            "final": final_text,
        }
        records.append(rec)
        if al.kind in _KIND_LABEL:
            findings.append(
                {"kind": al.kind, "ja": ja_text, "ai": ai_text, "final": final_text, "episode": ep_dir.name}
            )
        if text_changed or al.kind in _KIND_LABEL:
            rec["item_id"] = len(items) + 1
            items.append(
                {
                    "id": rec["item_id"],
                    "ja": ja_text,
                    "ai": ai_text,
                    "final": final_text,
                    "flag": _KIND_LABEL.get(al.kind, ""),
                }
            )

    # LLM classification in batches; failures degrade to unclassified items.
    cases: list[dict] = []
    rules: list[str] = []
    terms: list[dict] = []
    n_batches = (len(items) + batch_size - 1) // batch_size
    for bi in range(n_batches):
        batch = items[bi * batch_size : (bi + 1) * batch_size]
        _progress(f"[{ep_dir.name}] 审校批次 {bi + 1}/{n_batches}（{len(batch)} 条）…")
        try:
            data = parse_learning_json(chat(settings, CLASSIFY_SYSTEM, build_classify_prompt(batch)))
        except Exception as e:
            logger.warning("classify failed for %s batch %d: %s", ep_dir.name, bi + 1, e)
            _progress(f"[{ep_dir.name}]   ! 批次 {bi + 1} 分类失败（{e}），该批仅记录结构差异")
            continue
        by_id = {it["id"]: it for it in batch}
        for c in data["cases"]:
            it = by_id.get(c.get("id"))
            if not it:
                continue
            case = {
                "ja": it["ja"],
                "ai": it["ai"],
                "final": it["final"],
                "categories": [str(x) for x in c.get("categories", [])],
                "note": str(c.get("note", "")),
                "episode": ep_dir.name,
            }
            cases.append(case)
            by_id[it["id"]]["categories"] = case["categories"]
            by_id[it["id"]]["note"] = case["note"]
            if c.get("term_ja") and c.get("term_zh"):
                terms.append({"ja": str(c["term_ja"]), "zh": str(c["term_zh"]), "note": case["note"]})
        rules += [str(r) for r in data["rules"]]

    summary: dict[str, int] = {}
    for al in alignments:
        summary[al.kind] = summary.get(al.kind, 0) + 1
    summary["text_changed"] = sum(1 for r in records if r["ai"] != r["final"])

    analysis = {
        "episode": ep_dir.name,
        "cues": {"ja": len(ja), "ai": len(ai), "final": len(final)},
        "summary": summary,
        "records": records,
    }
    (ep_dir / ANALYSIS_FILE).write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    _progress(f"[{ep_dir.name}] 分析完成：{summary}；错误案例 {len(cases)}、术语 {len(terms)}、规则 {len(rules)}")
    return {"analysis": analysis, "cases": cases, "terms": terms, "rules": rules, "findings": findings}


# ---------------------------------------------------------------------------
# project level
# ---------------------------------------------------------------------------


def scan_episodes(project_dir: Path) -> list[Path]:
    ep_root = Path(project_dir) / "episodes"
    if not ep_root.is_dir():
        return []
    return sorted(d for d in ep_root.iterdir() if d.is_dir() and (d / AI_FILE).exists())


def learn_project(project_dir: Path, settings: TranslateSettings) -> tuple[int, list[tuple[str, str]]]:
    """Analyze every episode, merge into knowledge/, refresh reports.
    Returns (episodes_done, [(episode, error)])."""
    project_dir = Path(project_dir)
    episodes = scan_episodes(project_dir)
    if not episodes:
        _progress(f"{project_dir}/episodes/ 下没有可学习的剧集目录")
        return 0, []

    store = MemoryStore.load_or_empty(project_dir)
    done = 0
    failed: list[tuple[str, str]] = []
    for i, ep_dir in enumerate(episodes):
        _progress(f"===== 剧集 {i + 1}/{len(episodes)}：{ep_dir.name} =====")
        try:
            result = analyze_episode(ep_dir, settings)
            store.add_cases(result["cases"])
            store.add_terms(result["terms"])
            store.add_rules(result["rules"])
            store.add_findings(result["findings"])
            done += 1
        except Exception as e:
            logger.exception("learn failed for %s", ep_dir)
            failed.append((ep_dir.name, str(e)))

    store.save()
    _progress(
        f"知识库已更新：案例 {len(store.cases)}、术语 {len(store.terms)}、"
        f"规则 {len(store.rules)}、结构发现 {len(store.findings)}"
    )
    write_reports(project_dir, store, episodes)
    return done, failed


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


def write_reports(project_dir: Path, store: MemoryStore, episodes: list[Path]) -> None:
    """reports/episode_report.csv (per case) + reports/project_report.html."""
    rdir = Path(project_dir) / "reports"
    rdir.mkdir(parents=True, exist_ok=True)

    with (rdir / "episode_report.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["episode", "categories", "ja", "ai", "final", "note"])
        for c in store.cases:
            w.writerow(
                [
                    c.get("episode", ""),
                    ";".join(c.get("categories", [])),
                    c.get("ja", ""),
                    c.get("ai", ""),
                    c.get("final", ""),
                    c.get("note", ""),
                ]
            )

    cat_counts: dict[str, int] = {}
    for c in store.cases:
        for cat in c.get("categories", []):
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
    kind_counts: dict[str, int] = {}
    for f_ in store.findings:
        kind_counts[f_.get("kind", "?")] = kind_counts.get(f_.get("kind", "?"), 0) + 1

    def esc(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = "".join(
        f"<tr><td>{esc(cat)}</td><td>{n}</td></tr>" for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1])
    )
    kind_rows = "".join(
        f"<tr><td>{esc(_KIND_LABEL.get(k, k))}</td><td>{n}</td></tr>" for k, n in sorted(kind_counts.items())
    )
    term_rows = "".join(
        f"<tr><td>{esc(t.get('ja', ''))}</td><td>{esc(t.get('zh', ''))}</td><td>{esc(t.get('note', ''))}</td></tr>"
        for t in store.terms
    )
    case_rows = "".join(
        f"<tr><td>{esc(c.get('episode', ''))}</td><td>{esc(','.join(c.get('categories', [])))}</td>"
        f"<td>{esc(c.get('ja', ''))}</td><td>{esc(c.get('ai', ''))}</td>"
        f"<td>{esc(c.get('final', ''))}</td><td>{esc(c.get('note', ''))}</td></tr>"
        for c in store.cases[:200]
    )
    rule_items = "".join(f"<li>{esc(r)}</li>" for r in store.rules)
    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>字幕学习报告</title>
<style>body{{font-family:sans-serif;max-width:1100px;margin:2em auto;padding:0 1em}}
table{{border-collapse:collapse;width:100%;margin:1em 0}}td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}
th{{background:#f0f0f0}}h2{{margin-top:2em}}</style></head><body>
<h1>字幕翻译学习报告</h1>
<p>剧集 {len(episodes)} 个 ｜ 错误案例 {len(store.cases)} 条 ｜ 术语 {len(store.terms)} 条 ｜
规则 {len(store.rules)} 条 ｜ 结构发现 {len(store.findings)} 条</p>
<h2>错误类别分布</h2><table><tr><th>类别</th><th>次数</th></tr>{rows}</table>
<h2>结构/时间轴差异</h2><table><tr><th>类型</th><th>次数</th></tr>{kind_rows}</table>
<h2>术语库</h2><table><tr><th>日文</th><th>中文</th><th>备注</th></tr>{term_rows}</table>
<h2>翻译规则</h2><ul>{rule_items}</ul>
<h2>错误案例（前 200 条）</h2>
<table><tr><th>剧集</th><th>类别</th><th>日文</th><th>AI</th><th>人工</th><th>说明</th></tr>{case_rows}</table>
</body></html>"""
    (rdir / "project_report.html").write_text(html, encoding="utf-8")
    _progress(f"报告已写出：{rdir}/episode_report.csv, project_report.html")
