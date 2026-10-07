from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    # 1. 验证 index.html 默认范围为 Zed 管理
    index_html = (WEBUI_DIR / "index.html").read_text(encoding="utf-8")
    assert '<option value="zed" selected>Zed 管理</option>' in index_html

    # 2. 验证 app.js 包含层级 optgroup 与默认 scope="zed" 逻辑
    app_js = (WEBUI_DIR / "app.js").read_text(encoding="utf-8")
    assert 'groupZed.label = "Zed 管理 (ACP)"' in app_js
    assert 'groupExt.label = "外部发现"' in app_js
    assert 'el.scope.value = p.get("scope") || "zed"' in app_js
    assert 'state.scope !== "zed"' in app_js

