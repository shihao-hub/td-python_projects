"""CLI 人读渲染辅助：表格版式与时间格式（与归档基线版式一致）。"""

from __future__ import annotations

from pathlib import Path


def fmt_dt(iso: str | None) -> str:
    return iso[:16].replace("T", " ") if iso else "-"


def render_thread_table(items: list[dict]) -> None:
    if not items:
        print("(no threads)")
        return
    w_title, w_agent = 56, 12
    print(f"{'TITLE':<{w_title}}  {'AGENT':<{w_agent}}  {'ARCH':<4}  {'UPDATED':<16}  PROJECTS")
    for t in items:
        title = (t["title"] or "(untitled)")[: w_title - 1]
        projects = ", ".join(Path(p).name for p in t["projects"])
        print(
            f"{title:<{w_title}}  {t['agent_id'][:w_agent]:<{w_agent}}"
            f"  {'*' if t['archived'] else '':<4}  {fmt_dt(t['updated_at']):<16}  {projects}"
        )
    print(f"\n{len(items)} thread(s)")


def render_project_table(items: list[dict]) -> None:
    if not items:
        print("(no projects)")
        return
    print(f"{'ACTIVE':>6}  {'ARCH':<4}  {'LAST ACTIVITY':<16}  PATH")
    for p in items:
        print(f"{p['active']:>6}  {p['archived']:>4}  {fmt_dt(p['last_activity']):<16}  {p['path']}")


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "-"
    return fmt_dt(ms[:19]) if isinstance(ms, str) else str(ms)


def render_session_table(items: list[dict]) -> None:
    if not items:
        print("(no sessions)")
        return
    print(f"{'SESSION':<26}  {'AGENT':<10}  {'ARCH':<4}  {'ZED':<4}  {'UPDATED':<16}  TITLE")
    for s in items:
        title = (s["title"] or "(untitled)")[:44]
        zed = "Y" if s.get("zed_thread_id") else "-"
        arch = "*" if s.get("archived") else ""
        print(
            f"{s['external_id'][:26]:<26}  {(s.get('agent') or '-')[:10]:<10}"
            f"  {arch:<4}  {zed:<4}  {fmt_dt(s.get('updated_at')):<16}  {title}"
        )
    print(f"\n{len(items)} session(s)")


def render_session_detail(s: dict) -> None:
    model = s.get("model") or {}
    model_disp = f"{model.get('provider') or '-'}/{model.get('model_id') or '-'}"
    if model.get("variant"):
        model_disp += f" ({model['variant']})"
    print(f"session   : {s['external_id']}")
    print(f"title     : {s['title'] or '(untitled)'}")
    print(f"directory : {s.get('directory') or '-'}")
    print(f"agent     : {s.get('agent') or '-'}  model: {model_disp}")
    print(f"created   : {fmt_dt(s.get('created_at'))}")
    print(f"updated   : {fmt_dt(s.get('updated_at'))}")
    print(f"archived  : {'yes' if s.get('archived') else 'no'}")
    print(f"zed link  : {s.get('zed_thread_id') or '(not linked)'}")


def render_content_text(content: dict) -> None:
    """sessions content 人读消息流（仿 export_markdown 的可读结构）。"""
    s = content
    print(f"=== {s['title'] or '(untitled)'} ===")
    print(f"session {s['external_id']}  agent={s.get('agent') or '-'}")
    print()
    for m in s.get("messages", []):
        who = "user" if m["role"] == "user" else m["role"]
        print(f"-- [{who}] {fmt_dt(m.get('created_at'))}"
              f"  agent={m.get('agent') or '-'}  model={m.get('model_id') or '-'}")
        texts = [p for p in m.get("parts", []) if p.get("type") == "text" and (p.get("text") or "").strip()]
        if texts:
            for p in texts:
                print(p["text"].rstrip())
        else:
            print("(no text content)")
        print()


_EFFORT_HEADER = "── {title}（按会话数降序，档位 max>high>medium>low>default）──"


def _human_size(n: int) -> str:
    const = 1024
    if n < const:
        return f"{n}B"
    div, exp = const, 0
    while n // div >= const:
        div *= const
        exp += 1
    return f"{n / div:.1f}{'KMGTPE'[exp]}B"


def render_search_result(result: dict) -> None:
    """检索结果人读版式：每条命中两行（主行 + 项目/id 上下文行）。"""
    query = result.get("query") or {}
    criteria = [f"archived={query.get('archived')}"]
    if query.get("q"):
        criteria.insert(0, f"q={query['q']!r}")
    if query.get("agent"):
        criteria.append(f"agent={query['agent']}")
    if query.get("project"):
        criteria.append(f"project={query['project']!r}")
    if query.get("since"):
        criteria.append(f"since={str(query['since'])[:10]}")
    if query.get("until"):
        criteria.append(f"until={str(query['until'])[:10]}")
    if query.get("include_unlinked"):
        criteria.append("include_unlinked=true")
    print("search: " + "  ".join(criteria))

    for note in result.get("degraded") or []:
        print(f"! {note}")

    hits = result.get("hits") or []
    if not hits:
        print("(no matches)")
        return

    w_kind, w_agent, w_match = 12, 14, 16
    print(f"{'KIND':<{w_kind}}  {'AGENT':<{w_agent}}  {'A':<1}  {'MATCH':<{w_match}}  {'UPDATED':<16}  TITLE")
    for h in hits:
        title = (h.get("title") or "(untitled)")[:48]
        print(
            f"{h['kind'][:w_kind]:<{w_kind}}  {(h.get('agent_id') or '-')[:w_agent]:<{w_agent}}"
            f"  {'*' if h.get('archived') else '':<1}  {','.join(h.get('matched_fields') or [])[:w_match]:<{w_match}}"
            f"  {fmt_dt(h.get('updated_at') or h.get('created_at')):<16}  {title}"
        )
        context = [", ".join(Path(p).name for p in h.get("projects") or []) or "-"]
        ids = "  ".join(x for x in (h.get("session_id"), h.get("thread_id")) if x)
        if ids:
            context.append(ids)
        print(f"{'':<{w_kind}}  {'':<{w_agent}}       {'':<{w_match}}  {'':<16}  └ {' · '.join(context)}")

    total, count = result.get("total", len(hits)), result.get("count", len(hits))
    if total > count:
        print(f"\n{count}/{total} hit(s)（已截断；--limit 0 查看全部）")
    else:
        print(f"\n{count} hit(s)")


def render_effort_report(rep: dict) -> None:
    """stats effort 人读版式（对齐 ocstat render.go）。"""
    is_full = rep.get("mode") == "full"
    db_disp = rep.get("db_path") or "-"
    home = str(Path.home())
    if db_disp.startswith(home):
        db_disp = "~" + db_disp[len(home):]
    mtime = fmt_dt(rep.get("db_mtime"))
    print(f"opencode 会话模型/思考档位统计")
    snap = " · [快照兜底]" if rep.get("using_snapshot") else ""
    size = _human_size(rep.get("db_size") or 0)
    print(f"db: {db_disp} ({size}, 修改于 {mtime}){snap}")
    parts = [f"会话 {rep.get('total', 0)}"]
    if is_full:
        parts.append(f"（无实际消息 {rep.get('no_msg_count', 0)}，有启动模型 {rep.get('startup_count', 0)}）")
    versions = rep.get("versions") or []
    ver = versions[0] if len(versions) <= 1 else f"{versions[0]}~{versions[-1]}" if versions else "-"
    integrity = "完整" if is_full else "降级"
    parts.append(f"· opencode 版本 {ver} · 数据 {integrity}")
    if rep.get("cfg_merged"):
        parts.append("· 档位已合并配置")
    print(" ".join(parts) + f" · 生成于 {fmt_dt(rep.get('generated_at'))}")
    if not is_full and rep.get("note"):
        print(f"⚠ {rep['note']}")
    print()
    title = "启动模型 × 档位" if is_full else "当前模型 × 档位（启动档位不可用）"
    print(_EFFORT_HEADER.format(title=title))
    groups = rep.get("groups") or []
    if not groups:
        print("（暂无数据）")
        print()
        return
    print(f"{'PROVIDER':<22}  {'MODEL':<24}  {'EFFORT':<14}  {'会话':>6}  {'占比':>6}")
    for g in groups:
        print(f"{g['provider'][:22]:<22}  {g['model'][:24]:<24}  {g['effort'][:14]:<14}  {g['sessions']:>6}  {g['percent']:>5}%")
    print(f"{'合计':<22}  {'':<24}  {'':<14}  {rep.get('total', 0):>6}  100%")
    print()
