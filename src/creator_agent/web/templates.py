"""HTML/CSS templates for the local web UI (server-rendered, no JS framework).

Two pages:
- :func:`render_list`  - grid of video cards with cover, title, likes, status.
- :func:`render_detail` - video player (Range-streamed), metadata, full transcript.

All user-supplied text (titles, descriptions, tags) is HTML-escaped. The video
element is wired so clicking a transcript timestamp seeks the player.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from creator_agent.models.creator import Creator
    from creator_agent.models.transcript import Transcript
    from creator_agent.models.video import Video

_STATUS_BADGE: dict[str, str] = {
    "ASR_DONE": "ok",
    "VIDEO_DOWNLOADED": "dl",
    "METADATA_SAVED": "meta",
    "NEW": "new",
}

_STATUS_LABEL: dict[str, str] = {
    "ASR_DONE": "已转写",
    "VIDEO_DOWNLOADED": "已下载",
    "METADATA_SAVED": "仅元数据",
    "NEW": "新建",
}


def _esc(text: str | None) -> str:
    return html.escape(text or "")


def _fmt_num(n: int | None) -> str:
    """824 -> '824'; 12345 -> '1.2万' (Douyin-style)."""
    if not n:
        return "0"
    if n < 10_000:
        return str(n)
    return f"{n / 10_000:.1f}万"


def _fmt_ts(seconds: float) -> str:
    """0.07 -> '00:00'; 366.0 -> '06:06'; >=1h -> 'H:MM:SS'."""
    s = max(0, int(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_date(dt: datetime | None) -> str:
    if not dt:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def _status_badge(status: str) -> str:
    cls = _STATUS_BADGE.get(str(status), "new")
    label = _STATUS_LABEL.get(str(status), str(status))
    return f'<span class="badge {cls}">{_esc(label)}</span>'


def _tags_html(tags: list[str]) -> str:
    if not tags:
        return ""
    chips = "".join(f'<span class="tag">#{_esc(t)}</span>' for t in tags)
    return f'<div class="tags">{chips}</div>'


_CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
       background: #0f1115; color: #e6e6e6; }
header { position: sticky; top: 0; z-index: 10; background: #161a21; border-bottom: 1px solid #262b35;
         padding: 14px 24px; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
header h1 { font-size: 18px; margin: 0; font-weight: 600; }
header .count { color: #8a93a3; font-size: 13px; }
header .spacer { flex: 1; }
header select, header input { background: #0f1115; border: 1px solid #2c323d; color: #e6e6e6;
                              border-radius: 8px; padding: 7px 10px; font-size: 13px; }
header input { width: 220px; }
header form.paste { display: flex; gap: 8px; align-items: center; }
header form.paste input { width: 320px; }
header form.paste button { background: #2563eb; border: none; color: #fff; border-radius: 8px;
                           padding: 7px 14px; font-size: 13px; cursor: pointer; }
header form.paste button:hover { background: #1d4ed8; }
main { padding: 24px; max-width: 1400px; margin: 0 auto; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 18px; }
.card { background: #181c24; border: 1px solid #262b35; border-radius: 12px; overflow: hidden;
        text-decoration: none; color: inherit; display: flex; flex-direction: column;
        transition: transform .12s, border-color .12s; }
.card:hover { transform: translateY(-3px); border-color: #3a4252; }
.card .thumb { aspect-ratio: 16/9; background: #0f1115; overflow: hidden; }
.card .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.card .body { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.card .title { font-size: 14px; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2;
               -webkit-box-orient: vertical; overflow: hidden; min-height: 2.8em; }
.card .meta { display: flex; gap: 12px; align-items: center; font-size: 12px; color: #8a93a3; }
.card .meta .likes { color: #ff5c7a; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
.badge.ok { background: #143a2a; color: #4ade80; }
.badge.dl { background: #1e2d4a; color: #60a5fa; }
.badge.meta { background: #3a2f1a; color: #fbbf24; }
.badge.new { background: #2a2a2a; color: #9ca3af; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { font-size: 11px; color: #7aa2ff; background: #1a2030; padding: 2px 7px; border-radius: 8px; }
.empty { text-align: center; color: #8a93a3; padding: 80px 0; }

/* detail */
.back { color: #8a93a3; text-decoration: none; font-size: 14px; }
.back:hover { color: #e6e6e6; }
.detail { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 24px; }
@media (max-width: 900px) { .detail { grid-template-columns: 1fr; } }
.player video { width: 100%; border-radius: 12px; background: #000; display: block; }
.panel { background: #181c24; border: 1px solid #262b35; border-radius: 12px; padding: 18px; }
.panel h1 { font-size: 18px; margin: 0 0 12px; line-height: 1.4; }
.stats { display: flex; flex-wrap: wrap; gap: 16px; margin: 12px 0; font-size: 14px; }
.stats span { color: #cfd3da; }
.stats .likes { color: #ff5c7a; }
.meta-row { font-size: 13px; color: #8a93a3; margin: 4px 0; }
.desc { font-size: 13px; color: #b8bcc4; margin: 12px 0; line-height: 1.6; white-space: pre-wrap; }
.original { display: inline-block; margin-top: 8px; color: #7aa2ff; font-size: 13px; text-decoration: none; }
.transcript { grid-column: 1 / -1; margin-top: 8px; }
.transcript h2 { font-size: 16px; margin: 0 0 12px; display: flex; align-items: center; gap: 12px; }
.transcript button { font-size: 12px; background: #262b35; color: #e6e6e6; border: 1px solid #3a4252;
                     border-radius: 6px; padding: 4px 10px; cursor: pointer; }
.seg { display: grid; grid-template-columns: 64px 1fr; gap: 12px; padding: 7px 0;
       border-bottom: 1px solid #232833; font-size: 14px; line-height: 1.6; }
.seg .ts { color: #7aa2ff; cursor: pointer; font-variant-numeric: tabular-nums; font-size: 13px;
           text-decoration: none; user-select: none; }
.seg .ts:hover { text-decoration: underline; }
.seg .txt { color: #d8dce4; }
.placeholder { color: #8a93a3; padding: 24px 0; text-align: center; }

/* run status */
.runbox { max-width: 720px; margin: 40px auto; background: #181c24; border: 1px solid #262b35;
          border-radius: 12px; padding: 24px; }
.runbox h1 { font-size: 18px; margin: 0 0 12px; }
.runbox .url { color: #8a93a3; font-size: 13px; word-break: break-all; margin-bottom: 16px; }
.runbox ul.stages { list-style: none; padding: 0; margin: 0 0 16px; }
.runbox ul.stages li { padding: 6px 0; border-bottom: 1px solid #232833; font-size: 14px; }
.runbox ul.stages li::before { content: "✓ "; color: #4ade80; }
.runbox .running::before { content: "… "; color: #fbbf24; }
.runbox .error { color: #f87171; font-size: 14px; margin: 12px 0; white-space: pre-wrap; }
.runbox .actions { display: flex; gap: 12px; margin-top: 16px; }
.runbox .actions a { color: #7aa2ff; font-size: 14px; text-decoration: none; }
.runbox .actions a:hover { text-decoration: underline; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>"
    )


def render_list(items: list[dict], creators: list[Creator]) -> str:
    """Server-rendered grid of video cards.

    ``items`` = ``[{"video": Video, "creator": Creator, "has_transcript": bool}, ...]``.
    """
    options = '<option value="">全部创作者</option>' + "".join(
        f'<option value="{_esc(c.id)}">{_esc(c.nickname)}</option>' for c in creators
    )
    cards: list[str] = []
    for it in items:
        v = it["video"]
        c = it["creator"]
        ts = _fmt_date(v.published_at)
        likes = _fmt_num(v.stats.likes)
        status = _status_badge(v.status)
        transcript_mark = '<span class="badge ok" title="已转写">文</span>' if it["has_transcript"] else ""
        cards.append(
            f'<a class="card" href="/video/{_esc(v.id)}" data-creator="{_esc(c.id)}" '
            f'data-search="{_esc(v.title)} {_esc(c.nickname)} {" ".join(v.tags)}">'
            f'  <div class="thumb"><img src="/cover/{_esc(v.id)}" loading="lazy" alt=""></div>'
            f'  <div class="body">'
            f'    <div class="title">{_esc(v.title)}</div>'
            f'    <div class="meta"><span class="likes">❤ {_esc(likes)}</span>'
            f"      <span>{_esc(ts)}</span>{status}{transcript_mark}</div>"
            f"    {_tags_html(v.tags)}"
            f"  </div>"
            f"</a>"
        )
    grid = "".join(cards) if cards else '<div class="empty">暂无已采集的视频。粘贴上方链接运行，或先运行 sync 采集。</div>'
    body = (
        "<header><h1>创作者视频库</h1>"
        f'<span class="count">{len(items)} 个视频</span>'
        '<form class="paste" method="post" action="/run">'
        '<input name="url" placeholder="粘贴抖音链接 / 分享口令，回车运行完整 pipeline" '
        'autocomplete="off" required>'
        "<button type=\"submit\">运行</button>"
        "</form>"
        '<div class="spacer"></div>'
        f'<select id="creatorFilter">{options}</select>'
        '<input id="search" placeholder="搜索标题 / 标签…" autocomplete="off">'
        "</header>"
        f'<main><div class="grid">{grid}</div></main>'
        "<script>"
        "const f=document.getElementById('creatorFilter'),s=document.getElementById('search');"
        "function apply(){const cf=f.value,q=s.value.toLowerCase();"
        "document.querySelectorAll('.card').forEach(c=>{const okC=!cf||c.dataset.creator===cf;"
        "const okS=!q||c.dataset.search.toLowerCase().includes(q);"
        "c.style.display=(okC&&okS)?'':'none';});}"
        "f.addEventListener('change',apply);s.addEventListener('input',apply);"
        "</script>"
    )
    return _page("创作者视频库", body)


def render_detail(video: Video, creator: Creator | None, transcript: Transcript | None) -> str:
    """Server-rendered video detail: player + metadata + full transcript."""
    nickname = _esc(creator.nickname) if creator else "未知创作者"
    stats = video.stats
    stats_html = (
        '<div class="stats">'
        f'<span class="likes">❤ {_esc(_fmt_num(stats.likes))}</span>'
        f"<span>💬 {_esc(_fmt_num(stats.comments))}</span>"
        f"<span>⭐ {_esc(_fmt_num(stats.favorites))}</span>"
        f"<span>↗ {_esc(_fmt_num(stats.shares))}</span>"
        "</div>"
    )
    original = (
        f'<a class="original" href="{_esc(str(video.video_url))}" target="_blank" rel="noopener">原视频 ↗</a>'
        if video.video_url
        else ""
    )

    if transcript and transcript.segments:
        segs = "".join(
            f'<div class="seg"><a class="ts" data-start="{seg.start:.3f}">{_fmt_ts(seg.start)}</a>'
            f'<span class="txt">{_esc(seg.text)}</span></div>'
            for seg in transcript.segments
        )
        transcript_html = (
            '<div class="transcript"><h2>解说文案 '
            '<button onclick="copyTranscript()">复制全文</button></h2>'
            f'<div id="segments">{segs}</div></div>'
        )
        # Hidden full text for the copy button.
        full_text = _esc(transcript.text)
    else:
        transcript_html = (
            "<div class='transcript'><h2>解说文案</h2>"
            "<div class='placeholder'>暂无文案（未转写）。运行 "
            "<code>creator-agent asr</code> 生成。</div></div>"
        )
        full_text = ""

    body = (
        '<header><a class="back" href="/">← 返回列表</a></header>'
        '<main><div class="detail">'
        '<div class="player">'
        f'<video id="player" controls preload="metadata" poster="/cover/{_esc(video.id)}">'
        f'<source src="/stream/{_esc(video.id)}" type="video/mp4">'
        "您的浏览器不支持 video 标签。</video>"
        "</div>"
        '<div class="panel">'
        f"<h1>{_esc(video.title)}</h1>"
        f"{stats_html}"
        f'<div class="meta-row">发布 {_fmt_date(video.published_at)} · 采集 {_fmt_date(video.collected_at)}</div>'
        f'<div class="meta-row">创作者：{nickname} · 平台 {_esc(video.platform)} · 状态 {video.status.value}</div>'
        f'<div class="desc">{_esc(video.description)}</div>'
        f"{_tags_html(video.tags)}"
        f"{original}"
        "</div>"
        f"{transcript_html}"
        "</div></main>"
        "<script>"
        "const player=document.getElementById('player');"
        "document.querySelectorAll('.seg .ts').forEach(a=>a.addEventListener('click',()=>{"
        "player.currentTime=parseFloat(a.dataset.start);player.play();window.scrollTo({top:0,behavior:'smooth'});"
        "}));"
        "function copyTranscript(){const t=document.getElementById('fulltext')?.textContent||'';"
        "navigator.clipboard.writeText(t).then(()=>{const b=event.target;b.textContent='已复制 ✓';"
        "setTimeout(()=>b.textContent='复制全文',1500);});}"
        "</script>"
        # Hidden full-text node the copy button reads from.
        f'<div id="fulltext" style="display:none">{full_text}</div>'
    )
    return _page(video.title, body)


def render_run(job: dict | None, submit_error: str | None = None) -> str:
    """Status page for a pasted-URL pipeline run. Auto-refreshes while running."""
    if submit_error:
        body = (
            '<header><a class="back" href="/">← 返回列表</a></header>'
            '<main><div class="runbox"><h1>无法提交任务</h1>'
            f'<div class="error">{_esc(submit_error)}</div>'
            '<div class="actions"><a href="/">返回列表</a></div>'
            "</div></main>"
        )
        return _page("运行失败", body)

    if not job:
        body = (
            '<header><a class="back" href="/">← 返回列表</a></header>'
            '<main><div class="runbox"><h1>暂无任务</h1>'
            '<div class="url">还没有提交过链接。回到首页，在顶部输入框粘贴抖音链接。</div>'
            '<div class="actions"><a href="/">返回列表</a></div>'
            "</div></main>"
        )
        return _page("运行状态", body)

    running = not job.get("done")
    error = job.get("error")
    stages = job.get("stages") or []
    stages_html = "".join(f"<li>{_esc(s)}</li>" for s in stages)
    if running:
        stages_html += '<li class="running">正在处理…</li>'

    if running:
        heading = "运行中…"
    elif error:
        heading = "运行失败"
    else:
        heading = "运行完成 ✓"

    result_html = ""
    if not running and not error:
        title = _esc(job.get("title") or "")
        marks = []
        if job.get("transcribed"):
            marks.append('<span class="badge ok">已转写</span>')
        result_html = (
            f'<div class="meta-row">{title} {" ".join(marks)}</div>'
            f'<div class="actions"><a href="/video/{_esc(job.get("video_id") or "")}">查看视频与文案 →</a>'
            '<a href="/">返回列表</a></div>'
        )
    elif error:
        result_html = f'<div class="error">{_esc(error)}</div><div class="actions"><a href="/">返回列表</a></div>'

    refresh = '<meta http-equiv="refresh" content="3">' if running else ""
    body = (
        '<header><a class="back" href="/">← 返回列表</a></header>'
        f'<main><div class="runbox"><h1>{heading}</h1>'
        f'<div class="url">{_esc(job.get("url") or "")}</div>'
        f'<ul class="stages">{stages_html}</ul>'
        f"{result_html}"
        "</div></main>"
    )
    return _page(f"运行状态 - {heading}", body).replace("<head>", f"<head>{refresh}", 1)
