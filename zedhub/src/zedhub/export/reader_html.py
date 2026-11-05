"""会话时间线 → 自包含单文件 HTML 阅读器。

设计目标：**双击即读**，不依赖任何静态资源服务器、CDN 或网络：
- 零外部依赖（CSS/JS 全内联）；
- 工具调用与结果默认折叠（``<details>``），正文不被日志淹没；
- 顶部搜索框按事件过滤并在正文中高亮，用户消息生成侧边目录；
- 明暗主题跟随系统，可手动切换并记忆；
- 打印时自动展开全部折叠块。

安全边界：所有会话文本先 ``html.escape`` 再做受限 Markdown 转换，
链接只允许 http/https，杜绝会话内容里的脚本注入。
"""

from __future__ import annotations

import html
import re

from ..core.handoff import Handoff
from ..core.trajectory import TimelineEvent

# 详略档位下单条工具结果字符上限（与 Markdown 导出同口径）
_RESULT_LIMIT = {"minimal": 0, "brief": 300, "full": 4000}
_ARG_LIMIT = {"minimal": 0, "brief": 600, "full": 4000}

_ROLE_LABEL = {
    "user": "用户", "assistant": "助手", "thinking": "思考",
    "system": "系统摘要", "error": "错误", "raw": "原始事件",
}
_ROLE_ICON = {
    "user": "👤", "assistant": "🤖", "thinking": "💭",
    "system": "📋", "error": "⚠️", "tool_call": "🔧",
    "tool_result": "📤", "raw": "❔",
}

_CSS = """
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1e222b;--fg:#e6e6e6;--dim:#9aa3b2;
--line:#2a2f3a;--accent:#4ea1ff;--user:#1c2a3d;--tool:#1a2119;--sys:#2a2418;--err:#3a1d1d}
html[data-theme="light"]{--bg:#f7f8fa;--panel:#fff;--panel2:#f0f2f5;--fg:#1a1d23;
--dim:#5b6472;--line:#dfe3ea;--accent:#1f6feb;--user:#eaf2ff;--tool:#eef5ec;--sys:#fff7e0;--err:#fdecec}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header.top{position:sticky;top:0;z-index:9;background:var(--panel);
border-bottom:1px solid var(--line);padding:14px 22px}
header.top h1{margin:0 0 6px;font-size:19px;line-height:1.4}
.badges{display:flex;flex-wrap:wrap;gap:6px;font-size:12px;color:var(--dim)}
.badge{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
padding:1px 9px}
.badge.warn{color:#ffb454;border-color:#6b4a1f}
.toolbar{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.toolbar input{flex:1;min-width:220px;background:var(--panel2);border:1px solid var(--line);
color:var(--fg);border-radius:8px;padding:7px 11px;font-size:14px}
.toolbar button{background:var(--panel2);border:1px solid var(--line);color:var(--fg);
border-radius:8px;padding:7px 13px;cursor:pointer;font-size:13px}
.layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:24px;
max-width:1180px;margin:0 auto;padding:24px 22px 80px}
nav.toc{position:sticky;top:120px;align-self:start;max-height:calc(100vh - 150px);
overflow:auto;font-size:13px;border-right:1px solid var(--line);padding-right:14px}
nav.toc a{display:block;color:var(--dim);text-decoration:none;padding:3px 0;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
nav.toc a:hover{color:var(--accent)}
nav.toc .hint{color:var(--dim);font-size:12px;margin-bottom:8px}
@media(max-width:860px){.layout{grid-template-columns:1fr}nav.toc{display:none}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:18px 20px;margin-bottom:26px}
.card h2{margin:0 0 12px;font-size:15px;letter-spacing:.04em;color:var(--accent)}
.card ul{margin:6px 0 12px;padding-left:20px}
.card .kv{display:grid;grid-template-columns:88px 1fr;gap:4px 12px;font-size:13.5px}
.card .kv dt{color:var(--dim)}
.card .kv dd{margin:0;word-break:break-all}
.card .last{border-left:3px solid var(--accent);padding-left:12px;color:var(--dim);
margin-top:8px}
.card .last p{margin:.4em 0}
.card .last h3,.card .last h4{margin:.7em 0 .3em;font-size:14px;color:var(--fg)}
.card .last pre{max-height:300px;white-space:pre-wrap}
.card pre{background:var(--panel2);border-radius:8px;padding:12px;overflow:auto;
font-size:12.5px;max-height:420px;white-space:pre-wrap}
.timeline{display:flex;flex-direction:column;gap:14px}
.ev{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:14px 17px}
.ev-user{background:var(--user);border-color:#2b445f}
.ev-head{display:flex;gap:9px;align-items:center;font-size:12.5px;color:var(--dim);
margin-bottom:7px}
.ev-head .who{font-weight:600;color:var(--fg)}
.ev-body{white-space:normal;word-break:break-word}
.ev-body p{margin:.45em 0}
.ev-body h3,.ev-body h4{margin:.8em 0 .35em;font-size:15px}
.ev-body pre{background:var(--panel2);border-radius:8px;padding:11px;
overflow:auto;font-size:12.8px;line-height:1.55}
.ev-body code{background:var(--panel2);padding:1px 5px;border-radius:5px;font-size:12.8px}
.ev-body pre code{background:none;padding:0}
.ev-body a{color:var(--accent)}
details.ev-tool{background:var(--tool)}
details.ev-tool>summary{cursor:pointer;list-style:none;display:flex;gap:9px;
align-items:center;font-size:12.8px;color:var(--dim)}
details.ev-tool>summary::-webkit-details-marker{display:none}
details.ev-tool>summary .who{color:var(--fg);font-weight:600}
details.ev-tool[open]>summary{margin-bottom:9px}
.ev-sys{background:var(--sys)}
.ev-err{background:var(--err)}
.ev mark{background:#ffd166;color:#1a1d23;border-radius:3px}
.hidden{display:none!important}
.empty{color:var(--dim);text-align:center;padding:40px}
footer{color:var(--dim);font-size:12px;text-align:center;padding:20px}
@media print{
  header.top{position:static}nav.toc{display:none}.layout{display:block}
  details.ev-tool{display:block}details.ev-tool>summary{display:block}
  body{background:#fff;color:#000}
}
"""

_JS = """
(function(){
  var root=document.documentElement, saved=localStorage.getItem('zedhub-theme');
  if(saved) root.setAttribute('data-theme',saved);
  var btn=document.getElementById('theme-btn');
  btn.addEventListener('click',function(){
    var next=root.getAttribute('data-theme')==='light'?'dark':'light';
    root.setAttribute('data-theme',next);localStorage.setItem('zedhub-theme',next);
  });
  var q=document.getElementById('q'), evs=Array.prototype.slice.call(
    document.querySelectorAll('[data-search]'));
  var timer=null;
  function clearMarks(el){
    el.querySelectorAll('mark').forEach(function(m){
      m.replaceWith(document.createTextNode(m.textContent));
    });
    el.normalize();
  }
  function mark(el,needle){
    if(!needle) return;
    var walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,null);
    var nodes=[],n;while(n=walker.nextNode())nodes.push(n);
    nodes.forEach(function(node){
      var t=node.nodeValue, i=t.toLowerCase().indexOf(needle);
      if(i<0)return;
      var after=node.splitText(i);after.splitText(needle.length);
      var m=document.createElement('mark');m.textContent=after.nodeValue;
      after.parentNode.replaceChild(m,after);
    });
  }
  function apply(){
    var needle=(q.value||'').trim().toLowerCase();
    var visible=0;
    evs.forEach(function(el){
      clearMarks(el);
      var hit=!needle||(el.getAttribute('data-search')||'').indexOf(needle)>=0;
      el.classList.toggle('hidden',!hit);
      if(hit){visible++;mark(el,needle);}
    });
    var empty=document.getElementById('empty');
    if(empty) empty.classList.toggle('hidden',visible>0);
  }
  q.addEventListener('input',function(){
    clearTimeout(timer);timer=setTimeout(apply,160);
  });
  apply();
  document.querySelectorAll('nav.toc a').forEach(function(a){
    a.addEventListener('click',function(e){
      e.preventDefault();
      var t=document.getElementById(a.getAttribute('href').slice(1));
      if(t){t.scrollIntoView({behavior:'smooth',block:'start'});}
    });
  });
})();
"""


def _inline_md(text: str) -> str:
    """受限行内 Markdown：行内代码、粗体、链接（已转义文本作为输入）。"""
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", text)

    def link(m: re.Match[str]) -> str:
        label, url = m.group(1), html.unescape(m.group(2))
        # 允许 http(s) 与本地 file 链接（会话里常引用本地文件）；
        # 其他协议（javascript:/data: 等）只保留文字，防止注入。
        if not url.startswith(("http://", "https://", "file://")):
            return label
        return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{label}</a>'

    return re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link, text)


def md_to_html(text: str) -> str:
    """极简 Markdown → HTML（先转义，再放行有限语法）。

    只支持标题、代码块、列表、引用、行内代码/粗体/链接——足够阅读会话，
    不引入第三方渲染器。
    """
    if not text:
        return ""
    escaped = html.escape(text)
    blocks: list[str] = []
    fence = re.compile(r"```[^\n]*\n(.*?)```", re.S)
    pos = 0
    for m in fence.finditer(escaped):
        blocks.append(("text", escaped[pos:m.start()]))
        blocks.append(("code", m.group(1)))
        pos = m.end()
    blocks.append(("text", escaped[pos:]))

    out: list[str] = []
    for kind, body in blocks:
        if kind == "code":
            out.append(f"<pre><code>{body.rstrip()}</code></pre>")
            continue
        lines = body.split("\n")
        para: list[str] = []
        in_list = False

        def flush() -> None:
            nonlocal para
            if para:
                out.append("<p>" + _inline_md("<br>".join(para)) + "</p>")
                para = []

        def close_list() -> None:
            nonlocal in_list
            if in_list:
                out.append("</ul>")
                in_list = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                flush()
                close_list()
                continue
            heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
            if heading:
                flush()
                close_list()
                level = min(4, len(heading.group(1)) + 2)
                out.append(f"<h{level}>{_inline_md(heading.group(2))}</h{level}>")
                continue
            if stripped.startswith(("- ", "* ")):
                flush()
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                out.append(f"<li>{_inline_md(stripped[2:])}</li>")
                continue
            if stripped.startswith("&gt; "):
                flush()
                close_list()
                out.append(f"<blockquote>{_inline_md(stripped[5:])}</blockquote>")
                continue
            para.append(stripped)
        flush()
        close_list()
    return "\n".join(out)


def _fmt_ts(ts: str) -> str:
    return ts.replace("T", " ")[:16] if ts else ""


def _search_blob(*parts: str) -> str:
    return html.escape(" ".join(p for p in parts if p).lower(), quote=True)


def _render_card(handoff: Handoff) -> str:
    rows: list[str] = []
    if handoff.directory:
        rows.append(f"<dt>工作目录</dt><dd><code>{html.escape(handoff.directory)}</code></dd>")
    if handoff.created_at or handoff.updated_at:
        rows.append(
            f"<dt>时间</dt><dd>{html.escape(_fmt_ts(handoff.created_at))} → "
            f"{html.escape(_fmt_ts(handoff.updated_at))}</dd>"
        )
    rows.append(
        f"<dt>规模</dt><dd>{handoff.event_count} 事件 / {len(handoff.user_requests)} 轮用户输入"
        f" / {sum(handoff.tool_counts.values())} 次工具调用</dd>"
    )
    if handoff.error_count:
        rows.append(f"<dt>错误</dt><dd>{handoff.error_count} 次</dd>")
    if handoff.tool_counts:
        top = "、".join(f"{k}×{v}" for k, v in list(handoff.tool_counts.items())[:10])
        rows.append(f"<dt>工具分布</dt><dd>{html.escape(top)}</dd>")

    def listing(title: str, items: list[str], limit: int) -> str:
        if not items:
            return ""
        lis = "".join(f"<li><code>{html.escape(i)}</code></li>" for i in items[:limit])
        more = f"<li>…… 共 {len(items)} 条</li>" if len(items) > limit else ""
        return f"<h3>{title}</h3><ul>{lis}{more}</ul>"

    first = handoff.user_requests[0] if handoff.user_requests else "（未提取到）"
    warn = ""
    if handoff.reconstructed:
        warn = ('<p class="badge warn">⚠️ 启发式重建：无明文 transcript，'
                '正文由 protobuf 文本块还原</p>')
    parts = [
        '<section class="card" id="handoff">',
        "<h2>给接手者的快速上下文</h2>",
        warn,
        f"<p><strong>任务</strong>：{html.escape(first)}</p>",
        f'<dl class="kv">{"".join(rows)}</dl>',
        listing("涉及文件", handoff.files, 20),
        listing("执行命令", handoff.commands, 12),
    ]
    if handoff.last_assistant:
        parts.append("<h3>最后一条助手结论</h3>")
        parts.append(f'<div class="last">{md_to_html(handoff.last_assistant)}</div>')
    if handoff.progress_summary:
        parts.append("<h3>agy 进度摘要（CHECKPOINT）</h3>")
        parts.append(f"<pre>{html.escape(handoff.progress_summary)}</pre>")
    parts.append("</section>")
    return "\n".join(parts)


def _render_event(event: TimelineEvent, index: int, *, detail: str) -> str:
    result_limit = _RESULT_LIMIT[detail]
    arg_limit = _ARG_LIMIT[detail]
    ts = _fmt_ts(event.ts)
    when = f'<span class="time">{html.escape(ts)}</span>' if ts else ""

    if event.role == "tool_call":
        if arg_limit <= 0:
            return ""
        body = event.text[:arg_limit] + ("\n…[参数已截断]" if len(event.text) > arg_limit else "")
        name = html.escape(event.name or "tool")
        return (
            f'<details class="ev ev-tool" id="ev-{index}" '
            f'data-search="{_search_blob("工具调用", event.name, event.text[:2000])}">'
            f'<summary><span>🔧</span><span class="who">{name}</span>{when}</summary>'
            f"<pre><code>{html.escape(body)}</code></pre></details>"
        )
    if event.role == "tool_result":
        if result_limit <= 0:
            return ""
        body = event.text[:result_limit] + ("\n…[结果已截断]" if len(event.text) > result_limit else "")
        name = html.escape(event.name or "tool")
        return (
            f'<details class="ev ev-tool" id="ev-{index}" '
            f'data-search="{_search_blob("工具结果", event.name, event.text[:2000])}">'
            f'<summary><span>📤</span><span class="who">{name}</span>{when}</summary>'
            f"<pre><code>{html.escape(body)}</code></pre></details>"
        )
    if event.role == "raw" and detail != "full":
        return ""

    role = event.role
    extra = " ev-sys" if role == "system" else (" ev-err" if role == "error" else "")
    if role == "user":
        extra = " ev-user"
    label = _ROLE_LABEL.get(role, role)
    icon = _ROLE_ICON.get(role, "❔")
    body = md_to_html(event.text) if role not in ("raw",) else (
        f"<pre><code>{html.escape(event.text)}</code></pre>"
    )
    return (
        f'<div class="ev{extra}" id="ev-{index}" '
        f'data-search="{_search_blob(label, event.text[:4000])}">'
        f'<div class="ev-head"><span>{icon}</span><span class="who">{label}</span>{when}</div>'
        f'<div class="ev-body">{body}</div></div>'
    )


def _render_toc(events: list[TimelineEvent]) -> str:
    items: list[str] = []
    for index, event in enumerate(events):
        if event.role != "user":
            continue
        summary = " ".join(event.text.split())[:60] or "(空输入)"
        items.append(
            f'<a href="#ev-{index}">{html.escape(summary)}</a>'
        )
    if not items:
        return '<div class="hint">（无用户消息）</div>'
    return '<div class="hint">用户消息</div>' + "\n".join(items)


def render_html(handoff: Handoff, events: list[TimelineEvent], *,
                detail: str = "brief", source_name: str = "") -> str:
    """生成完整单文件 HTML。"""
    if detail not in _RESULT_LIMIT:
        detail = "brief"
    title = handoff.title or "(未命名会话)"
    badges = [
        f'<span class="badge">{(handoff.agent or "unknown")}</span>',
        f'<span class="badge">sid {html.escape(handoff.session_id[:8])}</span>',
        f'<span class="badge">{handoff.event_count} 事件</span>',
        f'<span class="badge">{sum(handoff.tool_counts.values())} 工具调用</span>',
    ]
    if handoff.directory:
        badges.append(f'<span class="badge">{html.escape(handoff.directory)}</span>')
    if handoff.reconstructed:
        badges.append('<span class="badge warn">启发式重建</span>')
    if source_name:
        badges.append(f'<span class="badge">{html.escape(source_name)}</span>')

    body_events = "\n".join(
        part for i, e in enumerate(events)
        if (part := _render_event(e, i, detail=detail))
    )
    return f"""<!doctype html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<header class="top">
  <h1>{html.escape(title)}</h1>
  <div class="badges">{"".join(badges)}</div>
  <div class="toolbar">
    <input id="q" type="search" placeholder="搜索本会话（Ctrl+F 亦可）…" autocomplete="off">
    <button id="theme-btn" type="button">切换主题</button>
  </div>
</header>
<div class="layout">
  <nav class="toc">{_render_toc(events)}</nav>
  <main>
    {_render_card(handoff)}
    <div class="timeline">
{body_events}
    </div>
    <div id="empty" class="empty hidden">没有匹配的事件</div>
    <footer>由 zedhub 导出 · 单文件自包含 · 只读快照</footer>
  </main>
</div>
<script>{_JS}</script>
</body>
</html>
"""


def write_html(handoff: Handoff, events: list[TimelineEvent], output: str,
               *, detail: str = "brief", source_name: str = "") -> int:
    """写入 HTML 文件，返回字节数（UTF-8 无 BOM）。"""
    data = render_html(handoff, events, detail=detail, source_name=source_name).encode("utf-8")
    with open(output, "wb") as fh:
        fh.write(data)
    return len(data)


__all__ = ["md_to_html", "render_html", "write_html"]
