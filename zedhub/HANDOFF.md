# HANDOFF.md — zedhub 交接文档

> 写给接手的 AI 或人。目标：读完即可理解项目全貌、约束与待办，不需要重新考古。
> 最后更新：2026-09-06（新增 RPC/MCP 协议入口时）。
> 本项目位于 `python_projects` 多项目仓库内（结构约定见上级目录的 AGENTS.md：子项目隔离、单一 git 仓库、只在子目录内工作）。
> `C:\WorkingProjects\zedhub` 是迁移前的旧副本，勿在那里继续开发。

## 项目主人（用户画像，重要）

- **后端程序员**，只关心后端与接口规约；前端对他来说无所谓（demo 级即可）
- 中文交流；偏好直接、简洁的回答
- 工作环境：**Windows + PowerShell 5.1**，无裸 `python`，统一用 **uv**（`uv run` / `uv sync` / `uvx`）
- 已有自己的 Zed 技能生态：`~/.agents/skills/` 下大量 `sh-*` 前缀的个人 skill（本项目的领域知识已沉淀为其中的 `sh-zed-session-db`）
- 沟通风格：认可"CLI 即接口/协议"的架构观，欣赏 JSON 信封规约、退出码语义这类明确的契约

## 项目是什么、为什么存在

**zedhub** = 对 Zed 编辑器本地 SQLite 数据库的**只读**查询 CLI，用于管理/检索 Zed 的 agent 会话记录（opencode / codex-acp / claude-acp 等）。

诞生背景（2026-09-01 真实事件）：用户在 Zed 中把两个项目文件夹合并成一个多根工作区窗口，会话按 `folder_paths` 重新归档"分家"；随后一次崩溃重启又把窗口恢复成单项目形态，41 条旧会话同时被标记 archived，历史列表看起来"全丢了"。经直接排查 SQLite 库确认数据完好并手工修复。本工具就是把那次排查经验产品化，让"会话找回/检索/统计"变成一条命令。

## 领域知识（不懂这些没法改这个项目）

- 数据库位置：`%LOCALAPPDATA%\Zed\db\0-stable\db.sqlite`（SQLite，**WAL 模式**）
- **读库铁律**：最新数据在 `-wal` 里，必须把三件套 `db.sqlite + -wal + -shm` 一起复制到临时目录再打开副本。全程绝不直接打开原库——这是本项目 `snapshot.py` 存在的唯一理由
- 核心表 `sidebar_threads`：每行一条会话。关键列：
  - `thread_id` BLOB 16 字节 → 对外统一转 uuid hex 字符串
  - `folder_paths` **换行符分隔**的多根工作区路径；`folder_paths_order` 形如 `'0,1'`
  - `archived` 1=历史隐藏；`title_override ?? title` 为显示标题
  - 时间列为 UTC ISO 字符串，**小数位可能 7 位以上**（`parse_ts` 已处理）
- 会话与项目是**多对多**：一条双路径会话同时归属两个项目
- schema 无官方保证（`migrations` 表会随 Zed 版本演进）→ `repo.py` 启动时 `PRAGMA table_info` 校验，缺列报 `SchemaError` 而不是瞎猜

## 架构与代码规约（改代码前必读）

```
src/zedhub/
├── cli.py                # typer 入口。文件头 docstring = 输出契约，改动前先读
├── api.py                # 方法注册表+方法元数据 = 三端(CLI/RPC/MCP)契约的单一事实源
├── rpc.py                # `zedhub rpc`：JSON-RPC 2.0 over line-delimited stdio(含 rpc.discover)
├── mcp_server.py         # `zedhub mcp`：MCP stdio server（依赖官方 mcp SDK 2.x）
├── core/
│   ├── snapshot.py       # open_snapshot() 上下文管理器：复制三件套→yield→清理
│   ├── model.py          # pydantic 模型 = 公共 JSON 契约；blob/时间/路径解析都在这
│   ├── repo.py           # 只读 SQL 层（mode=ro 打开快照）；唯一 SQL 在这
│   └── service.py        # 筛选/聚合/统计。全量 load 后 Python 过滤——表只有百级数据，别急着下推 SQL
├── export/demo.py        # JSON 注入模板；注意 `</` 转义防 </script> 逃逸（有测试盯住）
└── templates/demo.html   # 单文件 demo：内嵌 JSON + vanilla JS，零构建
```

**CLI 输出契约（用户明确认可，勿破坏向后兼容）**：
- 成功：stdout 单个 JSON `{"status":"ok","data":…,"count":…,"elapsed_ms":…}`
- datetime 一律**本地时区** ISO（曾出过 threads 本地/projects UTC 的不一致 bug，已修，勿回退）
- 退出码：0 成功（含空结果）/ 1 运行错误（stderr）/ 2 参数错误
- `--table` 为人类可读输出，格式不稳定，前端别依赖

**程序化协议契约（2026-09-06 新增，同样勿破坏）**：
- `zedhub rpc`：JSON-RPC 2.0，stdin 每行一个 request、stdout 每行一个 response；不支持 batch/位置参数；notification 不回包；错误码 -32700/-32600/-32601/-32602/-32603/-32000(快照/schema)/-32001(NotFound)
- `rpc.discover` 保留方法：返回 OpenRPC 风格 descriptor（方法/参数 schema/描述），由 api.METHOD_SPECS 生成；纯元数据**不走快照路径**，数据库缺失也可用
- `zedhub mcp`：MCP stdio server，4 个 tool 与 RPC 方法一一对应；域异常统一转 ToolError 透出原因
- 两端共享 `api.py` 注册表；payload 形状 = CLI 信封的 `data` 字段，三端不漂移
- 方法表与客户端接入配置：父仓库 `docs/projects/python_projects/zedhub/zedhub 对接协议.md`
- 已知 SDK 行为：mcp 2.x 对 list 返回值 structured_content 包成 `{"result": [...]}`，TextContent 只含首个元素——消费侧用 structured_content（测试 payload() helper 已处理）

**已知坑（都踩过，别再踩）**：
1. PowerShell 里 python `-c` 内联引号必炸 → 写 .py 文件再执行
2. GBK 控制台看 UTF-8 输出乱码 → `| Out-File -Encoding utf8` 落盘再看
3. naive vs aware datetime 比较会 TypeError → `service.list_threads` 入口已统一 `astimezone()`
4. `--db` 参数必须透传到 `open_snapshot(db)`（曾漏传导致测试误读真实库）

## 验证与工作流

```powershell
cd C:\WorkingProjects\language_projects\python_projects\zedhub
uv sync                                    # 装依赖（首次/换机/目录迁移后）
uv run pytest tests -q                     # 24 个用例，合成 fixture，绝不碰真实库
uv run zedhub threads list --table --limit 10   # 真实库冒烟（快照只读，Zed 开着也安全）
uv run zedhub export --out demo.html       # 重新生成 demo 页
```

项目迁移目录后：`uv sync` + `pytest` 通过即环境正常（uv.lock 无绝对路径依赖，模板经 `resources.files` 加载与位置无关；唯 `.venv` 内含绝对路径，迁移后必须重新 `uv sync` 重建）。

## 路线图（用户已确认的后续）

- **M3 写操作**（下一步）：`archive <id> [--undo]`、`rename <id> <title>`、路径归还/迁移。铁律照抄 `~/.agents/skills/sh-zed-session-db`：Zed 必须完全退出（`Get-Process` 检查）→ 带时间戳备份三件套 → 参数化 SQL → `PRAGMA wal_checkpoint(TRUNCATE)` → 重连校验
- **M4（可选）**：`serve` 子命令，FastAPI 包同一 core，localhost HTTP + SSE。浏览器无法直接调 CLI/MCP，这是 serve 仍存在的理由
- 长期边界：本库只是**元数据索引**，会话正文在外部 agent 自己的存储（opencode: `~/.local/share/opencode`，靠 `session_id` 对应）。"删除"要两头一起处理才有意义

## 相关外部资产

- 领域知识 skill：`C:\Users\shawn.zhang\.agents\skills\sh-zed-session-db\SKILL.md`（含完整修复套路与实战案例）
- 2026-09-01 修复时的库备份（临时目录，可能已清理）：`%TEMP%\opencode\zeddb_backup_20260901_175353`
- 用户真实数据规模（写作时）：247 会话 / 15 项目 / opencode 170 · codex-acp 57 · claude-acp 19 · grok-build 1
