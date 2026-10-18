import json
import os
import shutil
import subprocess
import sys
import threading
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


def launch_app_window(url: str) -> bool:
    """自动探测 Chrome / Edge 并以独立应用窗口 (--app) 启动（遵循 sh-localhost-pwa 规范）。"""
    # 1. 优先检查 PATH 中的环境变量
    candidates = ["chrome", "google-chrome", "google-chrome-stable", "chromium", "msedge", "microsoft-edge"]
    for c in candidates:
        found = shutil.which(c)
        if found:
            try:
                subprocess.Popen([found, f"--app={url}"])
                return True
            except OSError:
                continue

    # 2. Windows 常见物理安装路径探测
    if sys.platform == "win32":
        win_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for p in win_paths:
            if os.path.isfile(p):
                try:
                    subprocess.Popen([p, f"--app={url}"])
                    return True
                except OSError:
                    continue

    # 3. macOS 路径探测
    if sys.platform == "darwin":
        mac_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for p in mac_paths:
            if os.path.isfile(p):
                try:
                    subprocess.Popen([p, f"--app={url}"])
                    return True
                except OSError:
                    continue

    # 4. 回退：默认浏览器打开
    import webbrowser
    webbrowser.open(url)
    return False


def _query_daemon_if_available(cfg, query: str, top_k: int) -> Optional[SearchResponse]:
    """若 docsearch 后台常驻服务正在运行，优先通过 HTTP 接口秒级响应（免去重复加载模型）。"""
    import urllib.parse
    import urllib.request
    h = cfg.server.host
    p = cfg.server.port
    url = f"http://{h}:{p}/api/search?q={urllib.parse.quote(query)}&top_k={top_k}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "docsearch-cli"})
        with urllib.request.urlopen(req, timeout=1.5) as response:
            if response.status == 200:
                raw_json = json.loads(response.read().decode("utf-8"))
                return SearchResponse.model_validate(raw_json)
    except Exception:
        pass
    return None


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

    # 优先复用正在运行的后台服务（如有）；若未常驻则本地单次冷启动执行，即查即走
    from_daemon = False
    resp = _query_daemon_if_available(cfg, query=query, top_k=top_k)
    if resp is not None:
        from_daemon = True
    else:
        service = SearchService(cfg)
        resp = service.search(query=query, top_k=top_k)

    if json_output:
        print(resp.model_dump_json(indent=2))
        return

    from collections import defaultdict
    groups: Dict[str, List[SearchResultItem]] = defaultdict(list)
    for it in resp.results:
        groups[it.filepath].append(it)

    source_tag = "[cyan][后台服务直通][/cyan]" if from_daemon else "[green][独立进程·即查即退][/green]"
    console.print(
        f"\n[bold green]🔍 检索:[/bold green] [bold cyan]{resp.query}[/bold cyan]  "
        f"(耗时: {resp.took_ms:.1f}ms, 命中切块: {len(resp.results)}, 涉及文件: {len(groups)}, {source_tag})"
    )

    if not resp.results:
        console.print("[yellow]未找到相关文档，请尝试换一个描述。[/yellow]\n")
        return

    for filepath, chunks in groups.items():
        filename = chunks[0].filename
        max_score = max(c.score for c in chunks)
        title_text = f"📄 {filename}  [dim]({filepath})[/dim]"
        score_badge = f"[bold green]{max_score * 100:.1f}% 匹配[/bold green]"

        content_lines = []
        for c in chunks[:3]:
            head_info = f"[dim]L{c.start_line}-L{c.end_line} | {c.heading or '正文'} (相似度: {c.score * 100:.1f}%)[/dim]"
            preview_clean = c.content[:200].replace("\n", " ").strip()
            content_lines.append(f"{head_info}\n{preview_clean}...")

        if len(chunks) > 3:
            content_lines.append(f"[dim]... 其余 {len(chunks) - 3} 处匹配切块已折叠[/dim]")

        body = "\n\n".join(content_lines)
        console.print(Panel(body, title=f"{title_text} - {score_badge}", border_style="blue", padding=(0, 1)))
    print()


@app.command()
def index(
    force: bool = typer.Option(False, "--force", "-f", help="强制清空并全量重新构建索引"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """扫描所有配置目录并构建本地语义向量索引"""
    cfg = load_config(config_file)
    service = SearchService(cfg)
    console.print("[bold blue][*] 开始索引文档...[/bold blue]")
    t0 = time.time()
    st = service.index_all(force=force)
    took = time.time() - t0
    console.print(f"[bold green]✔ 索引构建完成！[/bold green] (耗时: {took:.2f}s)")
    console.print(f"  - 索引文件数: {st.total_files}")
    console.print(f"  - 向量切块数: {st.total_chunks}")


@app.command()
def status(
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """查看当前索引库状态与监听目录"""
    cfg = load_config(config_file)
    service = SearchService(cfg)
    st = service.get_stats()

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
    app_window: bool = typer.Option(False, "--app", help="以独立应用窗口 (--app) 启动 Chrome/Edge"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """启动 HTTP API 与内置 WebUI 服务（支持 --app 独立窗口模式）"""
    from docsearch.server import run_server
    cfg = load_config(config_file)
    h = host or cfg.server.host
    p = port or cfg.server.port
    url = f"http://{h}:{p}"

    if app_window:
        def _open_app():
            time.sleep(1.2)
            console.print(f"[bold cyan][*] 正在呼出 Chrome/Edge 独立应用窗口: {url}[/bold cyan]")
            launch_app_window(url)
        threading.Thread(target=_open_app, daemon=True).start()

    run_server(cfg, host=h, port=p)


@app.command()
def ui(
    host: Optional[str] = typer.Option(None, "--host", help="监听地址"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="监听端口"),
    browser: bool = typer.Option(False, "--browser", help="以常规浏览器标签页打开而非独立应用窗口"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="自定义配置文件路径"),
):
    """一键以桌面应用形态启动并打开 docsearch UI 界面"""
    serve(host=host, port=port, app_window=not browser, config_file=config_file)


@app.command()
def shortcut(
    desktop: bool = typer.Option(True, "--desktop/--no-desktop", help="在 Windows 桌面创建一键直达应用快捷入口"),
):
    """在桌面生成无黑框直达 Chrome 独立应用的启动脚本"""
    if sys.platform != "win32":
        console.print("[yellow]当前平台非 Windows，跳过创建快捷脚本。[/yellow]")
        return

    desktop_path = Path(os.path.expanduser("~/Desktop"))
    if not desktop_path.exists():
        console.print("[red]未找到桌面目录。[/red]")
        return

    vbs_path = desktop_path / "docsearch.vbs"
    project_dir = Path(__file__).resolve().parent.parent.parent
    uv_path = shutil.which("uv") or "uv"

    # 生成静默拉起脚本（零黑框）
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{project_dir}"
WshShell.Run "cmd /c {uv_path} run docsearch ui", 0, False
'''
    vbs_path.write_text(vbs_content, encoding="utf-8")
    console.print(f"[bold green]✔ 已在桌面生成直达应用脚本:[/bold green] {vbs_path}")
    console.print("[dim]提示：双击该图标即可后台静默拉起 docsearch 并打开 Chrome/Edge 独立应用窗口。[/dim]")


if __name__ == "__main__":
    app()
