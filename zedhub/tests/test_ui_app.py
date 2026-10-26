from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from zedhub.cli import _launch_app_window, app
from zedhub.webui import WEBUI_DIR, asset, index


def test_webui_serves_manifest_and_icon() -> None:
    # 1. 验证 index.html 文件存在且包含 manifest 与 icon 引用
    index_html = (WEBUI_DIR / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/ui/manifest.json">' in index_html
    assert '<link rel="icon" type="image/svg+xml" href="/ui/icon.svg">' in index_html

    # 2. 验证 manifest.json 静态配置规范
    manifest_file = WEBUI_DIR / "manifest.json"
    assert manifest_file.is_file()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest["short_name"] == "ZedHub"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/ui"

    # 3. 验证 icon.svg 内容合法
    icon_file = WEBUI_DIR / "icon.svg"
    assert icon_file.is_file()
    assert "<svg" in icon_file.read_text(encoding="utf-8")

    # 4. 验证 WebUI asset handler 允许正常访问 .json 与 .svg
    req_manifest = MagicMock()
    req_manifest.headers = {"host": "127.0.0.1:8766"}
    req_manifest.path_params = {"asset": "manifest.json"}
    resp_manifest = asyncio.run(asset(req_manifest))
    assert getattr(resp_manifest, "status_code", 200) == 200
    assert Path(resp_manifest.path).resolve() == manifest_file.resolve()

    req_icon = MagicMock()
    req_icon.headers = {"host": "127.0.0.1:8766"}
    req_icon.path_params = {"asset": "icon.svg"}
    resp_icon = asyncio.run(asset(req_icon))
    assert getattr(resp_icon, "status_code", 200) == 200
    assert Path(resp_icon.path).resolve() == icon_file.resolve()


def test_launch_app_window_calls_popen() -> None:
    with patch("shutil.which", return_value="C:\\fake\\chrome.exe"), \
         patch("subprocess.Popen") as mock_popen:
        success = _launch_app_window("http://127.0.0.1:8766/ui")
        assert success is True
        mock_popen.assert_called_once_with(["C:\\fake\\chrome.exe", "--app=http://127.0.0.1:8766/ui"])


def test_launch_app_window_fallback_when_none_found() -> None:
    with patch("shutil.which", return_value=None), \
         patch("os.path.isfile", return_value=False):
        success = _launch_app_window("http://127.0.0.1:8766/ui")
        assert success is False


def test_cli_ui_help_contains_app_option() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["ui", "--help"])
    assert result.exit_code == 0
    assert "--app" in result.stdout


def test_webui_default_scope_and_agent_hierarchy_contract() -> None:
    # 1. 验证 app.js 包含层级 optgroup 与默认 scope:zed 逻辑（路线 B）
    app_js = (WEBUI_DIR / "app.js").read_text(encoding="utf-8")
    assert 'groupZed.label = "Zed 管理 (ACP)"' in app_js
    assert 'groupExt.label = "外部发现"' in app_js
    assert 'optZedAll.value = "scope:zed"' in app_js
    assert 'optExtAll.value = "scope:external"' in app_js
    assert 'optAll.value = "all:all"' in app_js
    assert 'parseAgentChoice' in app_js
    assert 'badge(hit.mode, "mode")' in app_js


def test_webui_project_hierarchy_and_normalization_contract() -> None:
    # 验证 app.js 包含项目 optgroup 分组、路径规范化与会话统计逻辑
    app_js = (WEBUI_DIR / "app.js").read_text(encoding="utf-8")
    assert "function normPath(p)" in app_js
    assert "function setProjectValue(projVal)" in app_js
    assert 'groupZedProj.label = "Zed 管理项目"' in app_js
    assert 'groupExtProj.label = "外部发现项目"' in app_js
    assert "extProjectStats.get(key)" in app_js
    assert "diff = b.total - a.total" in app_js
    assert "diff = b.count - a.count" in app_js



def test_webui_theme_defaults_dark_and_can_switch_to_light() -> None:
    # 1. 默认暗色写死在 HTML 根节点，首帧前脚本只做「已存浅色」覆盖
    index_html = (WEBUI_DIR / "index.html").read_text(encoding="utf-8")
    assert '<html lang="zh-CN" data-theme="dark">' in index_html
    assert 'id="btn-theme"' in index_html
    assert 'localStorage.getItem("zedhub.theme")' in index_html

    # 2. 两套 token 都在 CSS 里，浅色仅在 data-theme=light 时生效
    css = (WEBUI_DIR / "style.css").read_text(encoding="utf-8")
    assert ':root[data-theme="light"]' in css
    assert "--bg: #121316" in css
    assert "--bg: #faf9f7" in css
    # 图标切换必须带 .icon-btn 前缀，否则会被 `.icon-btn svg` 的 display 覆盖成双图标叠加
    assert ".icon-btn .icon-moon { display: none; }" in css
    assert ':root[data-theme="light"] .icon-btn .icon-moon { display: block; }' in css

    # 3. 运行时切换：记忆偏好并同步 pywebview 标题栏
    app_js = (WEBUI_DIR / "app.js").read_text(encoding="utf-8")
    assert 'const THEME_KEY = "zedhub.theme"' in app_js
    assert "function applyTheme(theme, persist)" in app_js
    assert "window.pywebview" in app_js
    assert "pywebviewready" in app_js


def test_gui_titlebar_theme_degrades_without_window() -> None:
    # 1. 找不到窗口（或非 Windows）时安静返回 False，不抛异常
    from zedhub.gui.app import TitleBarTheme

    theme = TitleBarTheme(title="zedhub-theme-probe-no-such-window")
    assert theme.apply("dark") is False
    assert theme.set_theme("light") is False


def test_webui_index_build_uses_post_with_json_body() -> None:
    # 契约中 index/build 是 POST + body_model；用 GET 会被 404 兜底报
    # method_not_supported，因此前端必须走 POST 并携带 JSON 请求体。
    app_js = (WEBUI_DIR / "app.js").read_text(encoding="utf-8")
    assert 'async function api(path, params, payload)' in app_js
    assert 'init.method = "POST"' in app_js
    assert 'init.headers["Content-Type"] = "application/json"' in app_js
    assert "init.body = JSON.stringify(payload)" in app_js
    assert "api(`${API}/index/build`, null, { rebuild: false })" in app_js


def test_webui_app_js_is_syntactically_valid() -> None:
    # 字符串断言抓不到重复声明这类语法错误，整份 app.js 一旦解析失败，
    # 页面会静默停在初始空态（下拉/状态/列表都不加载），必须真解析一次。
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 app.js 语法校验")
    result = subprocess.run(
        [node, "--check", str(WEBUI_DIR / "app.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_gui_console_quit_hook_invokes_callback() -> None:
    # pywebview 主线程陷在 WinForms 消息循环时 SIGINT 不会派发；
    # ConsoleQuitHook 依赖 SetConsoleCtrlHandler 的独立线程回调打断。
    from zedhub.gui.app import ConsoleQuitHook

    calls: list[int] = []
    hook = ConsoleQuitHook(lambda: calls.append(1))
    assert hook._handle(0)  # 模拟 CTRL_C_EVENT：回调被执行返回真值
    assert calls == [1]

    if sys.platform == "win32":
        assert hook.install() is True
        hook.uninstall()
        hook.uninstall()  # 重复卸载是幂等的
