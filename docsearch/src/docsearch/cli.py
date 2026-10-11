import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from docsearch.config import load_config
from docsearch.service import SearchResponse, SearchResultItem, SearchService
from docsearch.watcher import DirectoryWatcher

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

app = typer.Typer(
    name="docsearch",
    help="本地文档与技能库轻量级语义检索系统",
    add_completion=False,
)
console = Console(no_color=False, highlight=False)


@app.command()
def search(
    query: Optional[str] = typer.Argument(None, help="模糊自然语言检索词"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="返回最匹配的结果数量"),
    json_output: bool = typer.Option(False, "--json", help="以结构化 JSON 输出"),
    schema_output: bool = typer.Option(False, "--schema", help="输出接口数据规范 JSON Schema"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """自然语言模糊语义检索文档（按文件聚合展示）"""
    if schema_output:
        schema_dict = {
            "name": "docsearch_search",
            "description": "自然语言模糊检索本地文档库",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索自然语言句子"},
                    "top_k": {"type": "integer", "default": 10, "description": "返回条目上限"},
                },
                "required": ["query"],
            },
            "response": SearchResponse.model_json_schema(),
        }
        print(json.dumps(schema_dict, indent=2, ensure_ascii=False))
        return

    if not query:
        console.print("[red]错误：请提供检索词，或使用 --schema 查看规范。[/red]")
        raise typer.Exit(code=1)

    cfg = load_config(config_file)
    service = SearchService(cfg)
    res = service.search(query, top_k=top_k)

    if json_output:
        print(json.dumps(res.model_dump(), indent=2, ensure_ascii=False))
        return

    if res.total == 0:
        console.print(f"[yellow]未找到与 '{query}' 相关的文档内容。请先运行 'docsearch index'。[/yellow]")
        return

    # 按文件聚合与去重
    groups: Dict[str, Dict] = {}
    for item in res.results:
        if item.filepath not in groups:
            groups[item.filepath] = {
                "filename": item.filename,
                "filepath": item.filepath,
                "max_score": item.score,
                "chunks": [],
            }
        g = groups[item.filepath]
        if item.score > g["max_score"]:
            g["max_score"] = item.score
        if not any(c.content.strip() == item.content.strip() for c in g["chunks"]):
            g["chunks"].append(item)

    file_count = len(groups)
    console.print(f"\n[bold green][检索词][/bold green] '{query}' (匹配 {file_count} 个文件)\n")

    for i, (fpath, g) in enumerate(groups.items(), start=1):
        score_pct = f"{g['max_score'] * 100:.1f}%"
        chunk_count = len(g["chunks"])
        title = f"[bold cyan]#{i} {g['filename']}[/bold cyan] [yellow]({chunk_count} 处片段)[/yellow] - 最高匹配度: [green]{score_pct}[/green]"

        body_lines = [f"[dim]路径:[/dim] [underline]{fpath}[/underline]\n"]
        for c_idx, c in enumerate(g["chunks"], start=1):
            c_score = f"{c.score * 100:.1f}%"
            h_text = c.heading or "正文"
            body_lines.append(f"[bold magenta]▶ 片段 {c_idx} · 章节: {h_text}[/bold magenta] [dim](行 {c.start_line}-{c.end_line} · {c_score})[/dim]")
            snippet = c.content.strip()
            if len(snippet) > 280:
                snippet = snippet[:280] + "..."
            body_lines.append(f"{snippet}\n")

        body_text = "\n".join(body_lines).strip()
        console.print(Panel(body_text, title=title, border_style="blue"))


@app.command()
def index(
    force: bool = typer.Option(False, "--force", "-f", help="强制重新索引所有文件（忽略 mtime 缓存）"),
    json_output: bool = typer.Option(False, "--json", help="以结构化 JSON 输出"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """扫描配置的目标目录并建立/更新向量索引"""
    cfg = load_config(config_file)
    service = SearchService(cfg)

    if not json_output:
        console.print("[bold cyan][*] 正在扫描配置目录并建立语义索引...[/bold cyan]")
        for d in cfg.indexing.watch_dirs:
            console.print(f"  - {d}")

    stats = service.index_all(force=force)

    if json_output:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    console.print(
        f"\n[bold green][OK] 索引构建完成！[/bold green] 新增/更新文件: {stats['indexed_files']} 个，切块: {stats['indexed_chunks']} 个。"
    )


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="以结构化 JSON 输出"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """查看当前索引统计与配置信息"""
    cfg = load_config(config_file)
    service = SearchService(cfg)
    st = service.get_stats()

    if json_output:
        print(json.dumps(st.model_dump(), indent=2, ensure_ascii=False))
        return

    table = Table(title="docsearch 系统状态")
    table.add_column("配置项", style="cyan")
    table.add_column("值", style="green")

    table.add_row("索引文件总数", str(st.total_files))
    table.add_row("向量切块总数", str(st.total_chunks))
    table.add_row("向量模型", st.model_name)
    table.add_row("向量库路径", st.db_dir)

    console.print(table)
    console.print("\n[bold]监听目录:[/bold]")
    for d in cfg.indexing.watch_dirs:
        console.print(f"  - {d}")


@app.command()
def watch(
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """常驻后台监听文件变更并自动更新索引"""
    cfg = load_config(config_file)
    service = SearchService(cfg)
    watcher = DirectoryWatcher(service)
    console.print("[bold green][*] 正在启动文件变动热监听守护进程...[/bold green]")
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]正在停止监听...[/yellow]")
        watcher.stop()


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host", help="监听地址"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="监听端口"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """启动 HTTP API 与内置 WebUI 服务"""
    from docsearch.server import run_server
    cfg = load_config(config_file)
    h = host or cfg.server.host
    p = port or cfg.server.port
    run_server(cfg, host=h, port=p)


if __name__ == "__main__":
    app()