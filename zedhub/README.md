# zedhub

Zed + OpenCode agent 会话统一工具：会话检索、完整内容查看、模型与 effort 统计、
导出、补登（link）与跨机器迁移。daemon 架构（《CLI 工具开发标准 v2》Python 试点）。

## 架构（v2 daemon 标准）

```
zedhub-gui    ← 桌面壳（pywebview + 系统托盘 + 内嵌 daemon；窗口指向 /ui）
zedhub serve  ← 唯一业务进程（daemon）：HTTP+JSON API（prod 固定 127.0.0.1:8766；
                dev 动态端口，实际地址见 <数据根>/runtime/daemon.json）
zedhub <cmd>  ← CLI 薄客户端：参数解析 → HTTP → 渲染
zedhub ui     ← 打开 daemon 托管的 Web 检索页 @ /ui（同样是薄客户端）
zedhub rpc    ← 兼容 JSON-RPC 薄壳（方法表冻结；rpc.discover 本地响应）
zedhub mcp    ← MCP 桥：stdio(MCP) ↔ HTTP daemon
（WebSocket 学习通道仅在 prod 默认开启 @ 127.0.0.1:8765，dev 默认关闭）
```

- Service 层（数据库访问、业务逻辑、写编排）**只存在于 daemon**；壳内只有
  API client 与渲染（CLI/rpc/MCP/Web 壳不 import `zedhub.core` 业务层）。
- 纯本地命令（`--help`、`--version`、`schema`、补全脚本）不触发 daemon、
  不访问数据库。
- **daemon 生命周期**：
  - 地址发现优先级：`--host` → `ZEDHUB_HOST` 环境变量 → 地址文件
    （`<通道目录>/runtime/daemon.json`）→ 通道默认（prod 固定 8766；
    dev 无默认端口，必须先 `zedhub serve`）；
  - **启动互斥**：`runtime/daemon.lock` OS 文件锁，进程退出自动释放；
    动态端口下不靠「端口占用」判定，避免两个 daemon 并存；
  - **自动拉起仅生产构建**（包版本为干净发布号）：连接失败且未显式指定地址时
    detach 拉起（启动期 stderr 落 `runtime/daemon.boot.log`），拉起互斥；
  - **开发构建一律不拉起**：报错并提示先 `zedhub serve`；
  - **buildID 握手**：壳请求带 `X-Zedhub-Build`；开发态 buildID 为源码指纹，
    改代码只重启一边会被当场检出（提示重启 daemon）；
  - **空闲退出**：被自动拉起的 daemon 空闲 30 分钟优雅退出并清理地址文件；
    前台 `serve` 不设空闲退出。
- **SSE 进度**：写类端点（link/export/import）在 `Accept: text/event-stream`
  时流式输出 `stage`/`result`/`error` 事件；CLI 人读实时渲染；断流不中止
  已开始的写流水线（结果照常落 operation journal）。

## 数据源边界

结构化会话查询当前仅实现 **OpenCode** 数据源（Zed 作为独立索引源）。Pi agent
未实现——查询返回 `source_not_supported`，不伪造数据。

**claude-code / codex / antigravity** 三源已注册为文件级源
（`capabilities=[export, link]`）：不支持会话查询，参与 `archive export`
跨机迁移（见下）与 `sessions link` 补登（见下）。

## 归档迁移（archive export / import）

- `archive export <project> -o a.db`（默认 opencode，**schema v1**）：
  结构化 5 表（Zed threads + OpenCode session/message/part），行为不变；
- `archive export <project> -o a.db --source claude-code|codex|antigravity`
  （**schema v2**）：以 Zed threads 为锚（`sidebar_threads` 按 agent_id 索引），
  agent 本地数据文件**整文件字节搬运**到 `agent_files` 表（不解析内容）：
  - claude-code：`~/.claude/projects/<slug>/<sid>.jsonl`；
  - codex：`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl`；
  - antigravity：`~/.gemini/antigravity-acp/conversations/<sid>.db`（旁带
    `.meta` 一并搬运）；
  - 本地已不存在的会话计入 `missing_session_count` 如实报告；
- `archive import a.db --target <dir>`：v1/v2 自动按 `schema_version` 分派；
  v2 apply 先写源数据文件（**目标文件已存在一律跳过**，不写坏在用文件），
  再补登 Zed threads（新 thread_id、session_id 原样保留、folder_paths 指向
  目标目录），重跑安全（幂等跳过）。

已知边界：
- antigravity 对话本体（`steps.step_payload`）是无公开 schema 的 protobuf，
  归档为整库字节，归档内不可结构化查询内容；
- 仅迁移 Zed 内使用过的会话（以 Zed threads 为锚），终端直跑的同 agent
  会话不在范围内；
- v2 import 不做源 agent 进程名检查（agent CLI 是 node 子进程，tasklist
  名匹配不可靠），靠「已存在即跳过」的文件级安全策略保证不写坏在用文件；
  Zed db 写入前的 zed/opencode 进程检查照常强制。

## 通道隔离（dev / prod，强约束）

数据目录、daemon 地址文件、实例锁、日志、索引库全部按通道分离，开发调试
绝不读写生产数据：

| 维度 | dev | prod |
|---|---|---|
| 判定 | 包版本含 `.dev` / `+`（无 tag，或 tag 后有新提交） | 干净发布号（HEAD 恰在 `zedhub-v*` tag） |
| 数据根 | `%APPDATA%\language_projects\zedhub\dev\` | `...\zedhub\prod\` |
| HTTP 端口 | 动态（0），实际值写地址文件 | 固定 `127.0.0.1:8766` |
| WS 冻结通道 | 默认关闭（`--ws-port 0`） | 固定 `127.0.0.1:8765` |
| 单实例 | `runtime/daemon.lock`（OS 文件锁） | 同左，不同目录天然隔离 |
| 桌面壳 | `gui.lock` + `window.json`，内嵌 dev daemon | 同左，prod daemon |
| 日志 | `<数据根>/runtime/daemon.log`（2 MB × 1 轮转） | 同左 |

- **覆盖开关**：`ZEDHUB_CHANNEL=dev|prod`（测试与排障用），未设置时按构建
  形态推导；
- **一次性迁移**：首次进入通道目录时，把整改前扁平目录里的 `index/` 与
  `operations/` **复制**过来（旧文件保留不删）；`backups/`、`snapshots/`
  体积大且可回溯，留在旧根目录；
- 快照、备份、operation journal、地址文件/日志/锁绝不写入 Zed/OpenCode
  数据库所在目录；
- **规范豁免**：zedhub 是 uv 运行的 Python CLI，不产 exe，go-wails 规范中的
  PE 图标 / `-H windowsgui` / 双 exe 产物 / 窗口几何记忆均不适用；适用的
  「`serve` 托管 WebUI」与「浏览器独立窗口（`ui --app`）」已对齐。

## 写操作安全

- 补登（`sessions link`）与归档导入（`archive import`）默认 **dry-run**，仅显式
  `--apply` 才写库；
- 写前检查 opencode/zed 进程退出（daemon 自身 zedhub.exe 不受误伤）；
- 备份三件套到数据目录 `backups/<operation_id>/`；
- 分库短事务 + `PRAGMA wal_checkpoint(TRUNCATE)` + 只读复查；
- **跨库不伪造原子性**：Zed 失败时 OpenCode 已提交的部分保留 operation
  journal（`partial`/`unknown` 状态）、ID 映射与备份，绝不虚报成功。

## 会话补登（sessions link）

把会话补登进 Zed 索引（`sidebar_threads` 新行：新 thread_id、沿用原
session_id，正文不断链），默认 dry-run：

- `sessions link <dir>`（默认 **opencode**）：查 OpenCode session 表；
- `--exact`：目录按**归一化后全等**匹配（默认子串匹配会带出子目录），
  与 `archive export --exact` 同语义；
- `sessions link <dir> --source claude-code|codex|antigravity`：浅层扫描
  各源用户级数据根（只读会话文件头部与尾部残段，不解析正文）：
  - claude-code：`~/.claude/projects/*/<sid>.jsonl`（行内 `cwd` 锚定目录，
    首条真人 user 消息作标题，行内时间戳）；
  - codex：`~/.codex/sessions/**`（首行 `session_meta` 的 cwd/时间）；
  - antigravity：`~/.gemini/antigravity-acp/conversations/<sid>.meta` 的
    `cwd`；对话本体是 protobuf，标题留空，时间用 `.db` 文件 mtime；
  - 无法定位目录的会话（如 antigravity 无 `.meta`）计入 `no_directory`
    跳过，不猜测；
- **查重语义（全源统一）**：按 `(agent_id, session_id, 目标目录)` 三元组，
  同目录重复补登才跳过；同一会话可在另一个工作区目录再挂一份入口
  （原目录入口保留，两个工作区都能打开同一会话）：

```powershell
uv run zedhub sessions link language_projects --source antigravity            # dry-run
uv run zedhub sessions link .thirdparty --source antigravity --target <nanocode>  # 挂到另一工作区（--apply 才写）
uv run zedhub sessions link "D:\path\from" --target "D:\path\to" --exact      # 只认全等目录（--apply 才写）
```

- 已知边界：三源补登的 thread 一律 `archived=0`；环境注入块
  （`<environment_context>`、`# AGENTS.md instructions` 等）不作标题；
  空 title 从 Zed 索引既有行回填（antigravity 对话本体是 protobuf，
  扫描层拿不到标题），避免显示默认线程名；查重语义不再按源区分。

## WebSocket 冻结声明

prod 通道下 daemon 同时监听 `127.0.0.1:8765` 的 WebSocket 通道（仅
`threads.list` 与 `stats` 两个只读方法）；dev 通道默认关闭该通道以避开端口
冲突，需要时显式 `--ws-port 8765`。该通道是**一次性学习实现**：本轮实现后
冻结，不再新增任何方法或能力；生产与自动化用途一律使用 HTTP API。

## 会话检索（search + Web 检索页）

支持**元数据检索**（默认）与**正文全文检索**（SQLite FTS5 + 纯标准库 CJK/代码分词）：

- 语义（服务端唯一定义）：`q` 按空白切分为多关键词，**全部命中**（AND）；
  - 默认（元数据）：在标题/agent/线程 id/会话 id/项目路径任一字段做不区分大小写子串匹配；
  - 搜正文（`--content` 或 Web 端勾选「搜正文」）：联合检索会话内五大源（OpenCode/Claude Code/Codex/Antigravity/Pi）的高层纯文本消息正文，命中时附带上下文气泡摘录（snippets）；
  - `q` 为空即按过滤条件浏览；
- **Zed 索引是主表**：它代表「Zed 里看得见的会话」（opencode/claude-acp/codex-acp/
  antigravity-acp/pi-acp 等），OpenCode 会话按 `session_id` 关联补目录与模型；
  `--include-unlinked` 才额外列出未进 Zed 索引的 OpenCode 会话（默认隐藏，避免重复）；
- **正文 FTS5 索引库**：存储在 `<数据根>/index/content_search.db`，支持用户手动触发构建与 mtime 毫秒级增量跳过；
- 三个壳同源：CLI `zedhub search` / `zedhub index`、MCP `zedhub.search.sessions` / `zedhub.index.*`、Web 页 `/ui`。

```powershell
uv run zedhub index build                         # 构建或增量更新会话正文全文索引
uv run zedhub index status                        # 查看正文全文索引状态
uv run zedhub search zedhub                       # 元数据检索
uv run zedhub search "录制" --content             # 开启正文全文检索（带上下文气泡摘录）
uv run zedhub search "zedhub 搜索" --archived all  # 多关键词 AND + 含归档
uv run zedhub search --agent opencode --since 2026-09-01 --json
uv run zedhub ui --app                             # 启动并以 Chrome/Edge 独立应用窗口打开（可视化面板）
uv run zedhub ui                                  # 打开 Web 检索页（--print 只打印 URL）
```

## Web 检索页（`/ui`）

- **独立面板与 PWA**：支持 `zedhub ui --app` 自动检测 Chrome / Edge 并以独立应用窗口（无地址栏/沉浸面板）打开；同时提供 PWA `manifest.json` 与专属图标，可直接由浏览器安装至系统桌面；

- 地址：`http://127.0.0.1:<port>/ui`（prod 固定 8766；dev 动态，实际值见
  `<数据根>/runtime/daemon.json`；`zedhub ui` 会按地址发现打开正确地址）；
- 形态：搜索框（`/` 聚焦、`Esc` 清空、输入防抖）+ 过滤行（agent/项目/归档三态/起止日期/
  含未关联会话）+ 结果列表（标题命中高亮、agent 与来源徽章、项目路径）+ 详情面板
  （点开看所属会话；OpenCode 会话可点「加载正文」按需取前 50 条消息）；
  检索条件同步到 URL，刷新与分享都保持；
- 边界：静态资源随包分发（原生 HTML/CSS/JS，**零构建链、零新增进程**），由 daemon
  在 `/ui` 托管；与 API 同一安全防线（`Host` 必须回环，否则 403），响应 `no-store`；
- 页面只调 `/api/v1`（`/search`、`/stats`、`/projects`、`/threads/{id}`、
  `/sessions/{id}`、`/sessions/{id}/content`），不含任何业务逻辑。

## 桌面壳（`zedhub-gui`）

自有桌面壳，不再依赖 `Chrome --app` 借壳：

- **形态**：pywebview（WebView2）窗口 + 系统托盘；窗口只加载 daemon 的 `/ui`，
  不 import `core`、不直连数据库；
- **内嵌 daemon**：壳内后台线程跑同一 `serve_with_lifecycle`（同一 Handler）；
  已有 CLI `zedhub serve` 在跑时直接复用其地址，不重复起进程；
- **关窗行为**：点 X **销毁窗口**（WebView2 子进程随之退出并 `gc.collect()`，
  对齐 go-wails 的 WindowClosing 放行 + FreeOSMemory），进程留托盘；托盘
  「显示窗口」按上次几何重建窗口，只有「退出」才优雅停 daemon；
- **单实例**：`<数据根>/gui.lock`（OS 文件锁），重复启动弹提示后退出；
- **窗口几何**：`<数据根>/window.json`，移动/缩放 700 ms 防抖保存；最大化只记
  标记、不覆盖还原尺寸；离屏坐标（拔外接屏 / 最小化 -25600）不写回；
- **图标双通道**：dev 用 Python 双蛇图标（`docs/assets/.../python-default.ico`），
  release 用专属三环枢纽（青绿/黄绿/琥珀，避开 Google 四色与 Antigravity 的 A 形）；
  由 `scripts/render_icon.py` 生成 PNG/ICO/SVG，窗口、托盘、exe 三处同源。

```powershell
uv run zedhub-gui              # 开发态直接启动桌面壳（无需先 serve）
uv run scripts/render_icon.py  # 重新生成图标资产
```

## 构建（Nuitka 双通道 exe）

`bin/` 为构建产物目录（已 gitignore），产物与通道严格对应：

| 通道 | 产物 |
|---|---|
| dev | `bin/zedhub-gui-dev.exe` + `bin/zedhub-cli-dev.exe` |
| release | `bin/zedhub-gui.exe` + `bin/zedhub-cli.exe` |

```powershell
uv run scripts/build_exe.py            # 自动判定：无干净 tag → dev
uv run scripts/build_exe.py --release  # 仅 HEAD 命中 zedhub-v* 且工作区干净
uv run scripts/build_exe.py --dir      # standalone 目录版（默认 onefile）
```

- GUI 与 CLI 子系统不同：GUI 无控制台（`--windows-console-mode=disable`），CLI
  控制台；禁止合成单产物（GUI 子系统拿不到终端管道，会顶掉 prompt）；
- PE 版本 / 描述 / 图标由 `--file-version` / `--windows-icon-from-ico` 写入；
  dev 构建用 Python 双蛇图标（同时打包为运行期 `icon-dev.ico`），release 用专属图标；
- 首次构建需 MSVC（VS 2022 Build Tools），产物约 36 MB/个。

## 常用命令

```powershell
uv run zedhub-gui                            # 桌面壳（窗口 + 托盘，内嵌 daemon）
uv run zedhub serve                          # 前台启动 daemon
uv run zedhub sessions list --project xxx    # 会话列表（zed_linked 标记）
uv run zedhub sessions content ses_xxx --format markdown -o out.md
uv run zedhub search zedhub --archived all   # 元数据检索（CLI 壳）
uv run zedhub ui --app                       # 独立应用窗口打开可视化面板（Chrome/Edge）
uv run zedhub ui                             # 打开 Web 检索页
uv run zedhub stats effort                   # 启动模型 × 档位统计（--watch）
uv run zedhub sessions link <dir>            # 补登 dry-run（--apply 才写）
uv run zedhub sessions link <dir> --source antigravity   # 三源补登/跨目录挂载
uv run zedhub archive export <project> -o a.db
uv run zedhub archive export <project> -o a.db --source claude-code  # v2（codex/antigravity 同）
uv run zedhub archive import a.db --target <dir>                      # dry-run（--apply 才写）
uv run zedhub schema                         # 离线契约导出（不连 daemon）
```

完整契约（HTTP/MCP/WS/RPC/CLI 五通道）见
`docs/projects/python_projects/zedhub/zedhub 对接协议.md`，或 `zedhub schema`。

## 已知迁移说明（相对旧 zedhub）

- 查询/写命令改为经 daemon 执行：开发构建需先 `zedhub serve`，生产构建自动
  拉起；daemon 未运行时返回可诊断的连接错误（业务语义不变，空结果仍成功）；
- 旧 `zedhub rpc` 同样依赖 daemon（`rpc.discover` 除外，本地即可响应）；
- 旧命令（threads/projects/stats）输出信封与归档基线一致；新命令用
  `{"ok":true,"data":...}` 信封。

设计依据：`docs/projects/python_projects/zedhub/specs/01-zed-session-hub/`。
