# Douyin Video Downloader (CDP-based)

基于 Chrome DevTools Protocol (CDP) 的无损高清抖音视频下载工具。**uv 单脚本**形态：一个 `douyin_dl.py` 自带依赖声明，无需 `pyproject.toml` / `requirements.txt` / 虚拟环境目录。

## 工作原理

1. **持久化浏览器会话**：通过独立专用 Profile 运行 Chrome，登录状态与 Cookie 永久保留，不污染日常浏览器。
2. **底层网络流嗅探**：通过 CDP 连接（默认端口 9222），在网页视频播放时从浏览器底层资源通道（`<video>` 元素与 `performance.getEntriesByType('resource')`）截获无水印高清 MP4 CDN 直链。
3. **免签名免反爬**：不做 a_bogus / msToken 签名逆向，直接复用真实浏览器的播放鉴权与 Cookie。
4. **自动落盘**：默认保存到 `~/Downloads`，同名文件自动加 `_1`、`_2` 后缀，不覆盖既有文件。

## 依赖与单脚本形态

依赖通过文件头部的 PEP 723 内联元数据声明，`uv run` 会自动准备隔离环境：

```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["websocket-client>=1.8"]
# ///
```

- 推荐入口：`uv run douyin_dl.py ...`
- 若当前 Python 环境已装 `websocket-client`，`python douyin_dl.py ...` 同样可用。

## 使用方法

### 从一段文本里提取链接并下载

传入抖音分享文案即可，工具会自己找出其中的链接：

```powershell
cd D:\Users\language_projects\python_projects\douyin_downloader
uv run douyin_dl.py "8.74 S@Y.ZZ Uyt:/ :3pm 09/26 5 分钟学会写架构设计 https://v.douyin.com/EBgtkB68340/ 复制此链接，打开Dou音搜索，直接观看视频！"
```

规则：

- 文本中的**所有**链接都会被检查，抖音链接逐个下载（串行，复用同一个 Chrome 与标签页）；**非抖音链接跳过**，并在结果里明确列出（不会静默丢弃）。
- 支持多段参数（以换行拼接）、字面 `\n` 作为换行、以及从管道读入：

```powershell
Get-Content 文案.txt -Raw | uv run douyin_dl.py --json
uv run douyin_dl.py --json -            # '-' 显式表示从 stdin 读
```

- 短链（`v.douyin.com/xxxx`）会自动跟随重定向解析为 `/video/<id>`。
- **必须能提取到抖音链接**，否则按错误退出（无内置默认链接）。

### 机器可读输出与契约导出

```powershell
uv run douyin_dl.py --json "<文本>"     # stdout 仅一个 JSON 包络
uv run douyin_dl.py schema              # 导出 CLI 契约 JSON（零业务 I/O）
uv run douyin_dl.py --schema            # 等价写法
uv run douyin_dl.py --help
```

JSON 包络（仓库统一约定）：

```json
{"ok": true, "data": {"downloaded": [], "skipped": [], "summary": {"total": 0, "succeeded": 0, "failed": 0, "skipped": 0}}}
{"ok": false, "error": {"code": "no_url", "message": "输入文本中未发现任何链接"}, "data": {"downloaded": [], "skipped": [], "summary": {"total": 0, "succeeded": 0, "failed": 0, "skipped": 0}}}
```

- 成功项字段：`input_url` / `video_url` / `video_id` / `title` / `path` / `bytes`；失败项另带 `error{code,message,detail}`。
- 部分成功时 `ok:false` 同时携带完整 `data`（有效结果不丢）。
- 错误码：`no_input`、`no_url`、`no_douyin_url`、`chrome_launch_failed`、`invalid_url`、`stream_not_found`、`download_failed`。
- 退出码：`0` 全部成功；`1` 存在失败项或部分成功；`2` 参数错误、输入为空、或没有可处理的抖音链接。

### 可选参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--output-dir` | `~/Downloads` | 下载目录 |
| `--debug-port` | `9222` | Chrome CDP 调试端口 |
| `--profile-dir` | `%APPDATA%\language_projects\douyin_downloader\chrome-profile` | Chrome 专用 Profile |
| `--chrome` | `C:\Program Files\Google\Chrome\Application\chrome.exe` | Chrome 可执行文件 |
| `--json` / `--schema` | 关 | 机器输出 / 契约导出 |

## 数据目录

所有项目自有数据位于 `%APPDATA%\language_projects\douyin_downloader\`（取不到 `APPDATA` 时回退 `~/.language_projects/douyin_downloader/`）：

| 路径 | 内容 |
|---|---|
| `chrome-profile\` | Chrome 专用 Profile（登录态、Cookie、偏好）；其中 `Default\Preferences` 的下载目录指向 `--output-dir` |
| 下载产物 | 默认 `~/Downloads`（由 `--output-dir` 决定，不属于工具缓存） |

历史路径 `C:\Users\29580\.chrome-automation-profile` 已迁移到上表新位置。

## 实现要点

- **Chrome 启动逻辑内嵌在 `start_chrome()` 函数里**：原先的 `start_chrome.ps1` 内容已并入 `douyin_dl.py`，经 `-EncodedCommand`（UTF-16LE + base64）交给 PowerShell 执行，避免中文/引号/空格路径的转义问题；仍走 PowerShell 是因为 `Invoke-CimMethod Win32_Process Create` 属于「创建即返回」的非启动阻塞方式（`&` 调用符会挂住 Python 进程）。
- **单文件分层**：契约层（错误码/JSON 包络/schema）→ 基础设施层（Chrome/CDP/HTTP）→ Service 层（提链、归一化、批量编排，不碰标准流）→ CLI 适配层（参数解析、人读/JSON 渲染、退出码映射）。
- **不提供 MCP**：本工具是本地一次性下载动作，没有跨会话的状态查询需求，按《CLI 工具开发标准》§5.5 以 `interface: "cli"` 声明契约（`schema` 导出的是 CLI 契约，不是 MCP 工具目录）。

## 已知限制

1. **抖音风控可能返回「验证中间页」**：此时该条失败并给出 `stream_not_found`，需在弹出的 Chrome 窗口内人工完成验证滑块后重跑。工具不会伪造成功。
2. **短链解析依赖网络**：解析失败的抖音链接按 `invalid_url` 如实失败（不静默丢弃）。
3. **抓取依赖 Chrome 与 CDP**：Profile 首次使用或长时间未用后可能需要重新登录/验证。
4. **需要 Chrome 已安装**在 `--chrome` 指定的路径。
5. 标题含非法字符时会被替换为 `_`，文件名主干最长 50 字符。
