"""批量会话导出编排：Zed 线程 → 一整套可阅读、可交接的本地文件。

产物布局（目录由调用方指定，默认落在 zedhub 数据根的 ``exports/`` 下）::

    <output_dir>/
      index.html                 # 阅读入口：可搜索的会话列表
      index.md                   # 同内容 Markdown 目录（喂 AI）
      sessions/<日期>_<时间>_<标题>.md
      sessions/<日期>_<时间>_<标题>.html

单会话内容由 ``transcript_md`` / ``reader_html`` 渲染；本模块只负责
筛选、命名、写盘与索引编排，保持可单测（线程列表通过参数注入）。
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..core.agy_proto_text import load_agy_events
from ..core.handoff import build_handoff
from ..core.paths import data_subdir
from . import reader_html, transcript_md

# 进度回调（stage, detail, pct）
ProgressFn = Callable[[str, str, int | None], None]

# 连续空白压成下划线
_SPACE_RE = re.compile(r"\s+")
# 文件名主体长度上限（留出日期与扩展名空间）
_SLUG_MAX = 48
# 站点标题最大长度
_TITLE_MAX = 120


@dataclass
class ExportedSession:
    """单个会话的导出结果。"""

    session_id: str
    thread_id: str
    title: str
    created_at: str
    events: int
    reconstructed: bool
    md_path: str = ""
    html_path: str = ""
    bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "title": self.title,
            "created_at": self.created_at,
            "events": self.events,
            "reconstructed": self.reconstructed,
            "md_path": self.md_path,
            "html_path": self.html_path,
            "bytes": self.bytes,
        }


def default_export_dir(agent: str, *, stamp: datetime | None = None) -> Path:
    """默认导出目录：``<数据根>/exports/<agent>-<yyyymmdd>``（符合仓规）。"""
    day = (stamp or datetime.now()).strftime("%Y%m%d")
    return data_subdir("exports") / f"{agent}-{day}"


def _slugify(title: str, session_id: str) -> str:
    """标题 → 安全文件名主体；空标题回退 session_id 前缀。

    路径分隔符转成短横线以保留可读性（``go_projects\\deepseeksearch`` →
    ``go_projects-deepseeksearch``），其余非法字符压成下划线。
    """
    text = _SPACE_RE.sub("_", (title or "").strip())
    text = text.replace("\\", "-").replace("/", "-")
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text)
    text = text.strip("._- ")
    if not text:
        text = (session_id or "session")[:8]
    return text[:_SLUG_MAX]


def _file_stem(created_at: str, title: str, session_id: str) -> str:
    """``YYYY-MM-DD_HHMM_<标题>``；时间缺失时用 sid 前缀兜底。"""
    stamp = ""
    if created_at:
        try:
            stamp = datetime.fromisoformat(created_at).astimezone().strftime("%Y-%m-%d_%H%M")
        except ValueError:
            stamp = created_at[:16].replace("T", "_").replace(":", "")
    if not stamp:
        stamp = (session_id or "unknown")[:8]
    return f"{stamp}_{_slugify(title, session_id)}"


def _dedupe_stem(stem: str, session_id: str, used: set[str]) -> str:
    """同名文件加 sid 前缀去重（导出可重跑，不静默覆盖别的会话）。"""
    if stem not in used:
        used.add(stem)
        return stem
    candidate = f"{stem}_{(session_id or 'x')[:6]}"
    n = 2
    while candidate in used:
        candidate = f"{stem}_{(session_id or 'x')[:6]}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def _thread_created(thread: object) -> str:
    value = getattr(thread, "created_at", None)
    if isinstance(value, datetime):
        return (value.astimezone() if value.tzinfo else value).isoformat()
    return ""


def _thread_updated(thread: object) -> str:
    value = getattr(thread, "updated_at", None)
    if isinstance(value, datetime):
        return (value.astimezone() if value.tzinfo else value).isoformat()
    return ""


def _write_bytes(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(data)
    return len(data)


def _render_index_md(items: list[ExportedSession], *, agent: str, directory: str) -> str:
    lines = [
        f"# {agent} 会话导出",
        "",
        f"- **来源目录**：`{directory}`",
        f"- **会话数**：{len(items)}",
        f"- **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "| 时间 | 标题 | 事件 | 类型 | 文件 |",
        "|---|---|---:|---|---|",
    ]
    for item in items:
        kind = "重建" if item.reconstructed else "明文"
        title = (item.title or "(未命名)").replace("|", "\\|")
        lines.append(
            f"| {item.created_at[:16].replace('T', ' ')} | {title} | {item.events} | {kind} "
            f"| [md](sessions/{Path(item.md_path).name}) · "
            f"[html](sessions/{Path(item.html_path).name}) |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_index_html(items: list[ExportedSession], *, agent: str, directory: str) -> str:
    rows = []
    for item in items:
        title = html.escape(item.title or "(未命名)")
        when = html.escape(item.created_at[:16].replace("T", " "))
        kind = "重建" if item.reconstructed else "明文"
        badge = ('<span class="badge warn">重建</span>' if item.reconstructed
                 else '<span class="badge">明文</span>')
        md_name = html.escape(Path(item.md_path).name, quote=True)
        html_name = html.escape(Path(item.html_path).name, quote=True)
        rows.append(
            '<tr class="row" data-search="'
            + html.escape(f"{item.title} {item.created_at}".lower(), quote=True) + '">'
            + f'<td class="when">{when}</td>'
            + f'<td class="ttl"><a href="sessions/{html_name}">{title}</a></td>'
            + f"<td>{item.events}</td>"
            + f"<td>{badge}</td>"
            + f'<td class="links"><a href="sessions/{md_name}">md</a></td>'
            + "</tr>"
        )
    total = len(items)
    rebuilt = sum(1 for i in items if i.reconstructed)
    return f"""<!doctype html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(agent)} 会话导出</title>
<style>
:root{{--bg:#0f1115;--panel:#171a21;--panel2:#1e222b;--fg:#e6e6e6;--dim:#9aa3b2;
--line:#2a2f3a;--accent:#4ea1ff}}
html[data-theme="light"]{{--bg:#f7f8fa;--panel:#fff;--panel2:#f0f2f5;--fg:#1a1d23;
--dim:#5b6472;--line:#dfe3ea;--accent:#1f6feb}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
header{{position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--line);
padding:16px 22px;z-index:5}}
h1{{margin:0 0 6px;font-size:19px}}
.meta{{color:var(--dim);font-size:13px}}
.toolbar{{margin-top:10px;display:flex;gap:10px}}
input{{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--fg);
border-radius:8px;padding:8px 12px;font-size:14px}}
button{{background:var(--panel2);border:1px solid var(--line);color:var(--fg);
border-radius:8px;padding:8px 14px;cursor:pointer}}
main{{max-width:1100px;margin:0 auto;padding:20px 22px 60px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;color:var(--dim);font-weight:500;font-size:12.5px;
border-bottom:1px solid var(--line);padding:8px 10px}}
td{{border-bottom:1px solid var(--line);padding:9px 10px;vertical-align:top}}
th{{white-space:nowrap}}
td{{white-space:nowrap}}
td.ttl{{white-space:normal;min-width:280px}}
tr:hover td{{background:var(--panel)}}
td.when{{color:var(--dim);font-variant-numeric:tabular-nums}}
td.ttl a{{color:var(--fg);text-decoration:none}}
td.ttl a:hover{{color:var(--accent)}}
.badge{{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
padding:1px 9px;font-size:11.5px;color:var(--dim);white-space:nowrap}}
.badge.warn{{color:#ffb454;border-color:#6b4a1f}}
.links a{{color:var(--accent);text-decoration:none;margin-right:6px;font-size:12.5px}}
.hidden{{display:none}}
footer{{color:var(--dim);font-size:12px;text-align:center;padding:24px}}
</style>
</head>
<body>
<header>
  <h1>{html.escape(agent)} 会话导出</h1>
  <div class="meta">{html.escape(directory)} · {total} 个会话（{rebuilt} 个启发式重建）·
  生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
  <div class="toolbar">
    <input id="q" type="search" placeholder="搜索标题或时间…" autocomplete="off">
    <button id="theme-btn" type="button">切换主题</button>
  </div>
</header>
<main>
<table>
<thead><tr><th>时间</th><th>标题</th><th>事件</th><th>来源</th><th>AI</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
<div id="empty" class="hidden">没有匹配的会话</div>
<footer>由 zedhub 导出 · 点击标题进入阅读页，md 链接是给 AI 的交接文件</footer>
</main>
<script>
(function(){{
  var root=document.documentElement,saved=localStorage.getItem('zedhub-theme');
  if(saved)root.setAttribute('data-theme',saved);
  document.getElementById('theme-btn').addEventListener('click',function(){{
    var n=root.getAttribute('data-theme')==='light'?'dark':'light';
    root.setAttribute('data-theme',n);localStorage.setItem('zedhub-theme',n);
  }});
  var q=document.getElementById('q'),rows=Array.prototype.slice.call(
    document.querySelectorAll('tr.row')),empty=document.getElementById('empty');
  q.addEventListener('input',function(){{
    var n=(q.value||'').trim().toLowerCase(),shown=0;
    rows.forEach(function(r){{
      var hit=!n||(r.getAttribute('data-search')||'').indexOf(n)>=0;
      r.classList.toggle('hidden',!hit);if(hit)shown++;
    }});
    empty.classList.toggle('hidden',shown>0);
  }});
}})();
</script>
</body>
</html>
"""


def export_threads(
    threads: list[object],
    *,
    output_dir: str | Path,
    agent: str = "antigravity-acp",
    directory: str = "",
    detail: str = "brief",
    formats: tuple[str, ...] = ("md", "html"),
    home: Path | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """批量导出 Zed 线程为 Markdown / HTML。

    ``threads`` 为 ``core.model.Thread`` 列表（已由调用方按 project/agent/
    archived 过滤）；单会话解析失败只记录 skipped，不中断整批。
    """
    def emit(stage: str, detail_text: str, pct: int | None = None) -> None:
        if progress is not None:
            progress(stage, detail_text, pct)

    out_root = Path(output_dir)
    sessions_dir = out_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    items: list[ExportedSession] = []
    skipped: list[dict] = []
    used: set[str] = set()
    total = len(threads)
    emit("validate", f"{total} 个线程待导出（agent={agent}）", 5)

    for index, thread in enumerate(threads, start=1):
        session_id = str(getattr(thread, "session_id", "") or "")
        thread_id = str(getattr(thread, "id", "") or "")
        title = str(getattr(thread, "title", "") or "")
        created = _thread_created(thread)
        if not session_id:
            skipped.append({"session_id": "", "thread_id": thread_id,
                            "reason": "会话无 session_id"})
            continue
        events, reconstructed = load_agy_events(session_id, home=home)
        if not events:
            skipped.append({"session_id": session_id, "thread_id": thread_id,
                            "reason": "无 transcript 且无 steps.db"})
            continue
        meta = {
            "title": title, "directory": directory or
            (getattr(thread, "projects", None) or [""])[0],
            "agent": agent, "session_id": session_id, "thread_id": thread_id,
            "created_at": created, "updated_at": _thread_updated(thread),
        }
        handoff = build_handoff(events, meta=meta, reconstructed=reconstructed)
        stem = _dedupe_stem(_file_stem(created, handoff.title, session_id), session_id, used)

        item = ExportedSession(
            session_id=session_id, thread_id=thread_id, title=handoff.title,
            created_at=created, events=len(events), reconstructed=reconstructed,
        )
        written = 0
        if "md" in formats:
            md_path = sessions_dir / f"{stem}.md"
            written += _write_bytes(
                md_path, transcript_md.render_markdown(
                    handoff, events, detail=detail).encode("utf-8"))
            item.md_path = str(md_path)
        if "html" in formats:
            html_path = sessions_dir / f"{stem}.html"
            written += _write_bytes(
                html_path, reader_html.render_html(
                    handoff, events, detail=detail).encode("utf-8"))
            item.html_path = str(html_path)
        item.bytes = written
        items.append(item)
        emit("export", f"[{index}/{total}] {handoff.title[:40]}", 5 + int(index * 85 / max(total, 1)))

    emit("index", "生成索引页", 92)
    index_md = _render_index_md(items, agent=agent, directory=directory)
    _write_bytes(out_root / "index.md", index_md.encode("utf-8"))
    index_html = _render_index_html(items, agent=agent, directory=directory)
    _write_bytes(out_root / "index.html", index_html.encode("utf-8"))
    emit("verify", f"完成：{len(items)} 个会话，跳过 {len(skipped)} 个", 100)

    return {
        "output_dir": os.path.normpath(str(out_root)),
        "agent": agent,
        "session_count": len(items),
        "skipped_count": len(skipped),
        "total_bytes": sum(i.bytes for i in items),
        "reconstructed_count": sum(1 for i in items if i.reconstructed),
        "sessions": [i.to_dict() for i in items],
        "skipped": skipped,
    }


__all__ = ["ExportedSession", "default_export_dir", "export_threads"]
