# NUITKA 打包情况记录

记录本项目（douyin_dl，uv 单脚本 CLI）用 Nuitka 打包 Windows exe 的全部现状：工具链、构建方式、验证结果与已知边界。打包流程的日常操作只看本文第 2、3 节即可。

## 1. 工具链与首次打包留档

| 项 | 值 |
|---|---|
| 打包日期 | 2026-10-01（v2.1.0）；2026-10-02 重建至 v2.3.0；2026-10-03 重建至 v2.5.0（知乎提取）；2026-10-06 重建至 v2.6.0（产物按平台分子目录） |
| Nuitka | 4.2.2 |
| 编译解释器 | Python 3.13（uv 管理，脚本 PEP 723 环境隔离） |
| C 编译器 | MSVC cl 14.3（VS 2022 Build Tools，`D:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`） |
| 缓存 | clcache（Nuitka 自动使用，二次编译大幅加速） |
| 入口 | `douyin_dl.py`（VERSION 2.6.0，第三方依赖 `websocket-client>=1.8` + `beautifulsoup4>=4.12` + `html2text>=2024.2.26`） |
| 产物 | `dist\douyin_dl.exe`，onefile 单文件约 7.6 MB（v2.3.0 时 6.9 MB，知乎提取新增两个纯 Python 库约 +0.7 MB） |

## 2. 构建方式（固化脚本）

```powershell
cd D:\Users\language_projects\python_projects\douyin_downloader
uv run scripts\build_exe.py              # onefile 单文件（默认，带图标）
uv run scripts\build_exe.py --dir        # standalone 文件夹版（启动更快，分发为目录）
uv run scripts\build_exe.py --icon X.ico # 指定图标
uv run scripts\build_exe.py --no-icon    # 不带图标
```

脚本要点（改脚本前必读）：

- **编译环境必须与入口脚本的 PEP 723 依赖保持一致**（当前为 `websocket-client`、`beautifulsoup4`、`html2text`）：Nuitka 靠编译期解释器 import 定位第三方包，漏声明哪个，哪个就不会被打进 exe，运行时才报 ModuleNotFoundError。
- **`zstandard` 必须声明**：onefile 压缩依赖它，缺了体积从 6.8 MB 涨到 27.3 MB（功能不受影响）。
- 版本号从 `douyin_dl.py` 的 `VERSION` 常量正则解析，写入 `--product-version` / `--file-version`，与源码单一事实源，不重复维护。
- 关键 flags：`--standalone --onefile --windows-console-mode=force --include-package=websocket --nofollow-import-to=websocket.tests --noinclude-unittest-mode=nofollow --onefile-tempdir-spec={CACHE_DIR}/douyin_dl/{VERSION}`。`websocket.tests` 是依赖包自带的测试子包，排除以消 unittest 警告并瘦身。
  - `--onefile-tempdir-spec` 固定解压目录为 `%LOCALAPPDATA%\douyin_dl\<版本>`：只解压一次，缓存命中后启动约 165ms（默认 spec 每次重新解压为 500–1200ms）。变量是 Nuitka 4.x `{VAR}` 风格，可用名单见 `nuitka/options/PathSpecs.py`（有 `{CACHE_DIR}` 没有 `{CACHE}`；`{PROGRAM}` 是运行期绝对路径、只能放 spec 开头，故程序名写死）。
- 产物输出 `dist/`；构建中间物（`douyin_dl.build/`、`douyin_dl.dist/`、`douyin_dl.onefile-build/`）也落在 `dist/` 下，可整体删除。子仓 `.gitignore` 已排除 `dist/` 与 `*.exe`，打包产物不入库。

## 3. 图标

- 默认图标：父仓库 `docs\assets\projects\python_projects\python-default.ico`（Python 双蛇标志，16–256 共 7 帧透明底），脚本按「脚本位置向上两级推算仓库根」自动定位，找不到时告警跳过、不阻断构建。
- 资产与再生成链路见父仓库《python exe 默认图标》文档（`.scripts\gen_python_logo.py` + `.scripts\gen_icon.py`）。
- Nuitka 经 `--windows-icon-from-ico` 直接写 PE 资源，不需要 Go 侧 rsrc/.syso 那套复制流程。

## 4. 验证结果

### 4.1 首次打包实测（v2.1.0 / v2.3.0）

| 验证项 | 方法 | 结果 |
|---|---|---|
| 版本输出 | `dist\douyin_dl.exe --version` | `douyin_dl 2.1.0`，退出码 0 ✅ |
| 契约导出 | `dist\douyin_dl.exe schema` | JSON 正常，中文 UTF-8 无乱码 ✅ |
| 参数错误路径 | `dist\douyin_dl.exe "纯文本没有链接"` | 退出码 2，`no_url` ✅ |
| stdin 管道 + JSON | `echo <非抖音链接> \| dist\douyin_dl.exe --json -` | 包络正确，`skipped` 如实列出，退出码 2 ✅ |
| 图标嵌入 | `ExtractAssociatedIcon` | 32x32 提取成功 ✅ |
| 完整下载链路 | 未跑（会真下视频并拉起 Chrome 自动化窗口） | 需要时 `.\dist\douyin_dl.exe "<分享文案>"` 实测 |

### 4.2 v2.5.0（知乎提取）实测

| 验证项 | 方法 | 结果 |
|---|---|---|
| 版本输出 | `dist\douyin_dl.exe --version` | `douyin_dl 2.5.0`，退出码 0 ✅ |
| 契约导出 | `dist\douyin_dl.exe schema` | 含 `extracted` 数组、`summary.extracted`、`zhihu_not_logged_in` / `zhihu_extract_failed`、side_effects 含 zhihu.com / zhimg.com，退出码 0 ✅ |
| 新依赖入包 | `dist\douyin_dl.exe schema` 能跑通即证明 `bs4` / `html2text` 已随包（二者在模块顶层 import） | 无 ModuleNotFoundError ✅ |
| 未登录路径 | `dist\douyin_dl.exe --json "<知乎链接>"`（profile 无 z_c0） | 退出码 1，`zhihu_not_logged_in` 结构化失败，不崩溃 ✅ |
| 知乎真实提取 | 默认 `headless-new` 模式提取 `zhuanlan.zhihu.com/p/96956163` | `article.md` + 元信息块正常，退出码 0 ✅ |
| 无链接路径 | `dist\douyin_dl.exe --json "纯文本没有链接"` | 退出码 2，`no_url` ✅ |
| stdin 管道 + JSON | `"<无关链接>" \| dist\douyin_dl.exe --json -` | `skipped` 如实列出，`summary` 含 `extracted: 0`，退出码 2 ✅ |
| 完整视频链路 | 未跑（同 4.1，属抖音侧回归范围） | 需要时 `.\dist\douyin_dl.exe "<分享文案>"` 实测 |

### 4.3 v2.6.0（产物按平台分子目录）实测

| 验证项 | 方法 | 结果 |
|---|---|---|
| 版本输出 | `dist\douyin_dl.exe --version` | `douyin_dl 2.6.0`，退出码 0 ✅ |
| 契约导出 | `dist\douyin_dl.exe schema` | `side_effects.filesystem` 含根目录 + `douyin` / `bilibili` / `zhihu` 三个子目录 ✅ |
| 落盘布局 | `dist\douyin_dl.exe --json --output-dir <临时目录> "https://b23.tv/ApmE1Nd"` | 产物落 `<临时目录>\bilibili\*.mp4`（40 MB，audio=merged），退出码 0 ✅ |
| 抖音链路 | 未跑（子目录逻辑与 B 站同一编排层改动，风险低） | 需要时 `.\dist\douyin_dl.exe "<分享文案>"` 实测 |

## 5. 已知边界与注意事项

1. **目标机仍需安装 Chrome**：exe 只打包了 Python 运行时与依赖，Chrome 由 `--chrome`（默认 `C:\Program Files\Google\Chrome\Application\chrome.exe`）在运行期调用，这是工具设计使然。
2. **onefile 解压缓存**：解压目录固定在 `%LOCALAPPDATA%\douyin_dl\<版本>`，首次运行解压一次（约 0.7s），之后启动约 165ms；版本升级自动换新目录，**旧版本目录不会自动清理**，偶尔手删 `%LOCALAPPDATA%\douyin_dl\` 下的旧版本即可。追求零残留可用 `--dir` 文件夹版。
3. **杀软误报**：无签名编译型 exe（onefile bootstrap 尤甚）可能被杀软拦截，属固有现象，发送他人时提前说明。
4. **改代码必须重编译**：Nuitka 是真编译（Python → C → 机器码），不像解释运行那样改完即生效；日常使用推荐仍走 `uv run douyin_dl.py`，exe 仅作分发件。
5. Nuitka 免费版含编译水印；若未来商业闭源分发需核对许可条款。

## 6. 为什么选 Nuitka 而不是 PyInstaller（简记）

- 真编译不打包字节码：源码不随 exe 分发，反编译难度远高于 PyInstaller 的 pyc 提取。
- 依赖静态分析自动收集，无需 hidden-imports/hooks 补丁；语言语义（asyncio/生成器/traceback）兼容更完整。
- 图标与版本信息原生命令行写入，无需第三方资源工具。
- 代价：需要 MSVC、编译慢（改一行也要重编译）、工具链门槛高；onefile 需配 zstandard 才能有效压缩。本项目纯本地个人工具，编译时长与水印均可接受，故取 Nuitka。

## 7. 相关文件

| 文件 | 作用 |
|---|---|
| `scripts\build_exe.py` | 打包固化脚本（本文第 2 节命令的实现） |
| `douyin_dl.py` | 业务入口（VERSION 单一事实源） |
| 父仓库 `docs\assets\projects\python_projects\python-default.ico` | 默认图标权威源 |
| 父仓库 `docs\projects\python_projects\python exe 默认图标.md` | 图标流程文档（语言层通用） |
