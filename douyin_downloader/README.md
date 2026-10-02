# Douyin/Bilibili Video Downloader (CDP + API)

抖音 + B 站视频下载工具（uv 单脚本）。抖音走 CDP 无损高清直链嗅探；B 站走公开 API + 浏览器登录态（cookie）路线，清晰度跟随账号权益（1080P+）。**单文件形态**：一个 `douyin_dl.py` 自带依赖声明，无需 `pyproject.toml` / `requirements.txt` / 虚拟环境目录。

## 工作原理

1. **持久化浏览器会话**：通过独立专用 Profile 运行 Chrome（**默认 `--headless=new`，屏幕上零痕迹**——无窗口、无任务栏图标），登录状态与 Cookie 永久保留，不污染日常浏览器。被抖音拦截时自动回退 `background` 模式（真 Chrome + 窗口移到屏幕外）。
2. **抖音 — 底层网络流嗅探**：通过 CDP 连接（默认端口 9222），在网页视频播放时从浏览器底层资源通道（`<video>` 元素与 `performance.getEntriesByType('resource')`）截获无水印高清 MP4 CDN 直链。抖音是 DASH 音视频分离，`media-video-*` 与 `media-audio-*` 两条流分别截获后**按时间轴对齐**、经 `ffmpeg -c copy` 合并。
3. **抖音 — 请求头「录制 → 复放」**：导航**之前**先在同一个 CDP 连接上 `Network.enable`，从 `Network.requestWillBeSentExtraInfo` 录下该请求的真实请求头（含 Cookie 与真实 Referer），下载时原样复放，只剔除 `range / if-range / content-length / content-type / accept-encoding / accept / accept-language / host / connection` 以及 HTTP/2 伪头。历史实现是手拼 `Referer` + 写死 `Chrome/120` 的 UA，签名 CDN 链接与 UA 绑定时会失稳。
4. **抖音 — 免签名免反爬**：不做 a_bogus / msToken 签名逆向，直接复用真实浏览器的播放鉴权与 Cookie。
5. **B 站 — API + 登录态**：直接调 B 站公开 web API（view / playurl，免签名）拿 DASH 流地址，登录态经 CDP 从同一 Chrome Profile 读取（含 HttpOnly 的 SESSDATA），清晰度跟随账号权益；DASH 音视频分离流经 `ffmpeg -c copy` 无损合并。
6. **自动落盘**：默认保存到 `~/Downloads`，同名文件自动加 `_1`、`_2` 后缀，不覆盖既有文件。

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

传入抖音分享文案、B 站链接或裸 BV 号即可，工具会自己找出其中的链接并按域名自动分流：

```powershell
cd D:\Users\language_projects\python_projects\douyin_downloader

# 抖音分享文案
uv run douyin_dl.py "8.74 S@Y.ZZ Uyt:/ :3pm 09/26 5 分钟学会写架构设计 https://v.douyin.com/EBgtkB68340/ 复制此链接，打开Dou音搜索，直接观看视频！"

# B 站：短链 / 主站链接 / 裸 BV 号等效
uv run douyin_dl.py "https://b23.tv/ApmE1Nd"
uv run douyin_dl.py "BV1B6YR6gEyd"

# 混合输入：一条命令两类链接都下载
uv run douyin_dl.py "https://v.douyin.com/EBgtkB68340/ https://b23.tv/ApmE1Nd"
```

规则：

- 文本中的**所有**链接都会被检查：抖音与 B 站链接逐个下载（串行，复用同一个 Chrome 与标签页）；**不支持的链接跳过**，并在结果里明确列出（`reason: not_supported`，不会静默丢弃）。
- 裸 BV 号（`BV` + 10 位字母数字）与 URL 中已带的 BV 号自动去重，不会重复下载。
- B 站多 P 视频：链接带 `?p=N` 时只下指定 P；不带 `p` 且为多 P 时**全部下载**（每 P 一条记录，文件名带 `_P{n}`）。
- 支持多段参数（以换行拼接）、字面 `\n` 作为换行、以及从管道读入：

```powershell
Get-Content 文案.txt -Raw | uv run douyin_dl.py --json
uv run douyin_dl.py --json -            # '-' 显式表示从 stdin 读
```

- 抖音短链（`v.douyin.com/xxxx`）与 B 站短链（`b23.tv/xxxx`）都会自动跟随重定向解析。
- **必须能提取到抖音或 B 站链接**，否则按错误退出（无内置默认链接）。

### B 站首次登录引导

B 站走登录态路线（清晰度跟随账号权益），未登录时该批 B 站链接按 `bilibili_not_logged_in` 失败：

```powershell
uv run douyin_dl.py --headed "https://b23.tv/ApmE1Nd"
```

工具会弹出 Chrome 窗口并打开 bilibili.com，人工完成登录后**重跑命令**即可（Cookie 随专用 Profile 持久化并自动续期，之后无需再登录）。

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

- 成功项字段：`input_url` / `video_url` / `video_id` / `title` / `path` / `bytes`；失败项另带 `error{code,message,detail}`。B 站多 P 时 `downloaded` 含多条记录（`video_id` 形如 `BVxxx_p2`）。
- 部分成功时 `ok:false` 同时携带完整 `data`（有效结果不丢）。
- 错误码：`no_input`、`no_url`、`no_douyin_url`、`chrome_launch_failed`、`invalid_url`、`stream_not_found`、`download_failed`、`bilibili_not_logged_in`（B 站 profile 无 SESSDATA）、`bilibili_api_error`（view/playurl 调用失败）、`ffmpeg_merge_failed`（合并失败）。
- 退出码：`0` 全部成功；`1` 存在失败项或部分成功；`2` 参数错误、输入为空、或没有可处理的抖音/B 站链接。

### 可选参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--output-dir` | `~/Downloads` | 下载目录 |
| `--debug-port` | `9222` | Chrome CDP 调试端口 |
| `--profile-dir` | `%APPDATA%\language_projects\douyin_downloader\chrome-profile` | Chrome 专用 Profile |
| `--chrome` | `C:\Program Files\Google\Chrome\Application\chrome.exe` | Chrome 可执行文件 |
| `--headed` | 关 | 前台可见窗口模式（人工完成验证滑块 / 首次登录 B 站） |
| `--headless[=MODE]` | `new` | 启动模式：`new`=Chrome 新版无头（默认，屏幕零痕迹）、`old`=旧无头（已被抖音风控识别）、`background`=真 Chrome + 窗口移到屏幕外 |
| `--close-browser` | 关 | 关闭常驻的自动化 Chrome 实例后退出 |
| `--json` / `--schema` | 关 | 机器输出 / 契约导出 |

## 打包为 exe（Nuitka）

```powershell
cd D:\Users\language_projects\python_projects\douyin_downloader
uv run scripts\build_exe.py              # onefile 单文件 -> dist\douyin_dl.exe
uv run scripts\build_exe.py --dir        # standalone 文件夹版（启动更快）
```

- **前提**：MSVC（VS 2022 Build Tools，Nuitka 编译用），首次编译需数分钟。
- **产物**：`dist\douyin_dl.exe`；目标机无需 Python/uv，但**仍需已安装 Chrome**（`--chrome` 可指定路径）。
- **依赖要点**：打包脚本环境必须同时含 `nuitka` 与 `websocket-client`（脚本 PEP 723 头已声明）——Nuitka 靠编译期解释器定位第三方包，缺了不会被打进 exe。
- **图标**：默认带 Python 双蛇标志（父仓库 `python-default.ico`），详见父仓库《python exe 默认图标》文档；`--icon` / `--no-icon` 可覆盖。
- onefile 解压目录固定在 `%LOCALAPPDATA%\douyin_dl\<版本>`（只解压一次，后续启动约 165ms）；版本升级后旧缓存目录不自动清理，偶尔手删即可；无签名 exe 可能被杀软误报。
- `dist/` 与 `*.exe` 已被子仓 `.gitignore` 排除，属本地部署件，不入库。

## 数据目录

所有项目自有数据位于 `%APPDATA%\language_projects\douyin_downloader\`（取不到 `APPDATA` 时回退 `~/.language_projects/douyin_downloader/`）：

| 路径 | 内容 |
|---|---|
| `chrome-profile\` | Chrome 专用 Profile（登录态、Cookie、偏好）；其中 `Default\Preferences` 的下载目录指向 `--output-dir` |
| 下载产物 | 默认 `~/Downloads`（由 `--output-dir` 决定，不属于工具缓存） |

历史路径 `C:\Users\29580\.chrome-automation-profile` 已迁移到上表新位置。

## 实现要点

- **Chrome 启动逻辑内嵌在 `start_chrome()` 函数里**：原先的 `start_chrome.ps1` 内容已并入 `douyin_dl.py`，经 `-EncodedCommand`（UTF-16LE + base64）交给 PowerShell 执行，避免中文/引号/空格路径的转义问题；仍走 PowerShell 是因为 `Invoke-CimMethod Win32_Process Create` 属于「创建即返回」的非启动阻塞方式（`&` 调用符会挂住 Python 进程）。
- **单文件分层**：契约层（错误码/JSON 包络/schema）→ 基础设施层（Chrome/CDP/HTTP/ffmpeg）→ Service 层（提链、归一化、批量编排，不碰标准流）→ CLI 适配层（参数解析、人读/JSON 渲染、退出码映射）。
- **B 站链路（API + cookie 混合）**：`resolve_bilibili_url` 解析短链/裸 BV 号 → `fetch_bili_view`（标题 + 多 P pages）→ `fetch_bili_playurl`（DASH 流，qn=127 按登录权益下发）→ `pick_bili_streams`（video 取 id 最大档、audio 取首项；老视频无 DASH 时走 durl 单流兼容分支）→ 双流下载（必带 `Referer: https://www.bilibili.com/`，否则 CDN 403）→ `ffmpeg -c copy` 合并。登录态经 CDP `Network.getCookies` 读取（可读 HttpOnly cookie），与抖音共用同一 Profile。
- **不提供 MCP**：本工具是本地一次性下载动作，没有跨会话的状态查询需求，按《CLI 工具开发标准》§5.5 以 `interface: "cli"` 声明契约（`schema` 导出的是 CLI 契约，不是 MCP 工具目录）。

### 启动模式与「不打扰用户」

| 模式 | 触发 | 行为 |
|---|---|---|
| `headless-new`（默认） | 不带参数 / `--headless=new` | Chrome 新版无头。实测可过抖音风控，屏幕上**无窗口、无任务栏图标** |
| `background`（回退） | `--headless=background` 或默认模式被拦时自动回退 | 真 Chrome + `--window-position=-32000,-32000`（移到屏幕外）+ `--mute-audio` |
| `headed` | `--headed` | 前台可见窗口，供人工过滑块 / 登录 B 站 |
| `headless-old` | `--headless=old` | 旧无头。**已被抖音风控识别**，保留仅为对照 |

`background` 模式为什么用「移到屏幕外」而不是「压在窗口最底层」：实测把窗口压到 z-order 最底（`HWND_BOTTOM`）或设置 `WS_EX_NOACTIVATE` 后，窗口会被判定为不可见/不可激活，**抖音播放器因此不加载视频流**（`stream_not_found`）；`--disable-backgrounding-occluded-windows`、`--disable-renderer-backgrounding`、`--disable-features=CalculateNativeWinOcclusion` 三个 flag 也救不回来。移到屏幕外不产生遮挡，Chrome 照常渲染，流能正常抓到，同时用户屏幕上完全看不到。

### 抖音音视频合并

抖音是 **DASH 音视频分离**：`media-video-*` 与 `media-audio-*` 是两条独立流。`capture_stream_urls()` 扫 `performance` 资源记录后按 `media-audio` 分区成对取流（两者同属一份 manifest，时间轴天然对齐），下载后 `ffmpeg -c copy` 合并。`<video>` 元素 src 仅作视频兜底——实测它常指向另一路 rendition，优先用它会与音频失步。

**时间轴对齐**：合并前用 `probe_duration()` 取两条流时长，差值超过 100 ms 时按 `min(时长)` 加 `-t` 裁齐，从根上避免长的那条在尾部留下静音 / 冻结帧；取不到时长时退化为 `-shortest`。思路取自 FetchV 的 `audioVideoCopy()`（见 `docs`/调研报告）。

**兜底源不混配**：`capture_stream_urls()` 返回视频来源（`dash` / `element`）。若视频来自 `<video>` 元素兜底，则**主动放弃**与 DASH 音频配对——混用两路 rendition 会产生肉眼看不出的静默失步；此时该流自带音轨就记 `included`，否则记 `missing`。宁可明确无声，也不要静默失步。

> 历史 bug：早期版本在抓流时用 `!n.includes('media-audio')` 主动滤掉音频，且命中 `<video>` 元素就提前 return，导致**抖音视频永远无声**。修复时同时加了 `probe_has_audio()`：若抓到的流本身已含音轨（走 `<video>` 兜底时可能发生），直接落盘而不再合并，避免出双音轨。

## 已知限制

1. **抖音风控可能返回「验证中间页」**：此时该条失败并给出 `stream_not_found`。默认 `headless=new` 会**自动回退 `background`（真 Chrome）重试一遍**；若两者都没拿到流，用 `--headed` 重跑，在弹出的 Chrome 窗口内人工完成验证滑块后再重跑。工具不会伪造成功。
2. **抓流通道有已知盲区（重要）**：`capture_stream_urls()` 读的是 `performance.getEntriesByType('resource')`，即**文档时间线**。实测抖音的播放器有时走 MSE，媒体分片由页面内的 **Web Worker** 发出——Worker 的网络请求**不进入文档的 Resource Timing**，此时 Resource Timing 里 `douyinvod` 条目数为 **0**，而 CDP `Network` 事件能看到同样两条流。另需注意 Resource Timing 缓冲默认上限 250 条，页面静态资源多时会更早挤满。
   - 实测证据（同一条视频、同一时刻）：`Resource Timing douyinvod = 0` vs `CDP Network = 2`（一视频一音频），页面附着 11 个 target、其中 6 个是 `blob:` worker。
   - 因此**同一条链接可能这次成功、下次 `stream_not_found`**，取决于抖音这次给的是直链播放还是 MSE 播放。彻底的修法是改用 CDP `Network` 事件 + `Target.setAutoAttach` 覆盖 Worker target（尚未实施）。
3. **B 站需要登录态**：未登录（Profile 无 SESSDATA）时 B 站链接整批按 `bilibili_not_logged_in` 失败；SESSDATA 过期后同样处理，重新 `--headed` 登录一次即可。登录态质量决定清晰度档位（工具取服务端按权益下发的最高档）。
4. **合并依赖 ffmpeg**：抖音 DASH 音视频流与 B 站 DASH 流都靠 `ffmpeg -c copy` 合并（无重编码，秒级完成）；本机 PATH 需有 `ffmpeg`，缺失时按 `ffmpeg_merge_failed` 失败，临时流会自动清理。抖音若始终抓不到音频流，会降级为无声视频并在结果里标 `audio: "missing"`（不伪造成功）。
5. **Chrome 进程驻留后台**：运行结束后 Chrome 实例不退出（复用登录态与实例，后续运行秒连），属设计行为。结束方式：`--close-browser`（推荐），或任务管理器结束对应 Profile 的 `chrome.exe`。
6. **短链解析依赖网络**：解析失败的抖音/B 站短链按 `invalid_url` 如实失败（不静默丢弃）。
7. **抓取依赖 Chrome 与 CDP**：Profile 首次使用或长时间未用后可能需要重新登录/验证（抖音验证滑块、B 站登录各一次）。
8. **需要 Chrome 已安装**在 `--chrome` 指定的路径。
9. 标题含非法字符时会被替换为 `_`，文件名主干最长 50 字符；B 站多 P 追加 `_P{n}` 后缀。标题轮询取不到时退化为 `douyin_<视频ID>`。
