# zedhub

Zed + OpenCode agent 会话统一工具：会话检索、完整内容查看、模型与 effort 统计、
导出、补登（link）与跨机器迁移。daemon 架构（《CLI 工具开发标准 v2》Python 试点）。

## 架构（v2 daemon 标准）

```
zedhub serve  ← 唯一业务进程（daemon）：HTTP+JSON API @ 127.0.0.1:8766（唯一契约）
zedhub <cmd>  ← CLI 薄客户端：参数解析 → HTTP → 渲染
zedhub rpc    ← 兼容 JSON-RPC 薄壳（方法表冻结；rpc.discover 本地响应）
zedhub mcp    ← MCP 桥：stdio(MCP) ↔ HTTP daemon
（WebSocket 学习通道并存于 daemon 进程 @ 127.0.0.1:8765，见下文冻结声明）
```

- Service 层（数据库访问、业务逻辑、写编排）**只存在于 daemon**；壳内只有
  API client 与渲染（CLI/rpc/MCP 壳不 import `zedhub.core` 业务层）。
- 纯本地命令（`--help`、`--version`、`schema`、补全脚本）不触发 daemon、
  不访问数据库。
- **daemon 生命周期**：
  - 地址发现优先级：`--host` → `ZEDHUB_HOST` 环境变量 → 地址文件
    （`runtime/daemon.json`）→ 默认 `127.0.0.1:8766`；
  - **自动拉起仅生产构建**（包版本为干净发布号）：连接失败且未显式指定地址时
    detach 拉起（stderr 落 `runtime/daemon.log`），拉起互斥；
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

**claude-code / codex / antigravity** 三源已注册为 EXPORT-only 文件级源
（`capabilities=[export]`）：不支持会话查询，仅参与 `archive export` 跨机
迁移（见下）。

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

## 本地数据目录（强约束）

快照、备份、operation journal、daemon 地址文件/日志只写入
`%APPDATA%\language_projects\zedhub\`（无 APPDATA 时回退
`~/.language_projects/zedhub/`），绝不写入 Zed/OpenCode 数据库所在目录。

## 写操作安全

- 补登（`sessions link`）与归档导入（`archive import`）默认 **dry-run**，仅显式
  `--apply` 才写库；
- 写前检查 opencode/zed 进程退出（daemon 自身 zedhub.exe 不受误伤）；
- 备份三件套到数据目录 `backups/<operation_id>/`；
- 分库短事务 + `PRAGMA wal_checkpoint(TRUNCATE)` + 只读复查；
- **跨库不伪造原子性**：Zed 失败时 OpenCode 已提交的部分保留 operation
  journal（`partial`/`unknown` 状态）、ID 映射与备份，绝不虚报成功。

## WebSocket 冻结声明

daemon 同时监听 `127.0.0.1:8765` 的 WebSocket 通道（仅 `threads.list` 与
`stats` 两个只读方法）。该通道是**一次性学习实现**：本轮实现后冻结，不再
新增任何方法或能力；生产与自动化用途一律使用 HTTP API。

## 常用命令

```powershell
uv run zedhub serve                          # 前台启动 daemon
uv run zedhub sessions list --project xxx    # 会话列表（zed_linked 标记）
uv run zedhub sessions content ses_xxx --format markdown -o out.md
uv run zedhub stats effort                   # 启动模型 × 档位统计（--watch）
uv run zedhub sessions link <dir>            # 补登 dry-run（--apply 才写）
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
