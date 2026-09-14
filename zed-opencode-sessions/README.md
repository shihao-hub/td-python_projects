# zed-opencode-sessions

MCP server 和 CLI 工具，用于查询和迁移 Zed 编辑器 + OpenCode 的会话数据。

## 功能

- **MCP Server**：供 AI 客户端（Zed/opencode/Claude Desktop）调用
  - `list_sessions`：列出指定项目目录的 opencode 会话摘要
  - `get_session_content`：根据 session_id 获取完整对话内容
- **CLI 工具**：人工调试和查询
  - `zoc list`：列出会话
  - `zoc show`：显示会话详情
  - `zoc mcp`：启动 MCP stdio server
- **导出脚本**：将会话内容导出为 Markdown 文档
- **跨机器迁移**：完整导出/导入会话到新电脑
- **补登脚本**：把某目录下已存在但未被 Zed 索引的 opencode 会话补登进 Zed（只写 Zed，复用原 session_id）

## 安装

```bash
cd zed-opencode-sessions
uv sync
```

## 使用

### CLI 模式

```bash
# 列出项目下的会话（project 为位置参数，不支持 --project）
uv run zoc list language_projects

# 包含已归档会话
uv run zoc list language_projects --archived

# 输出 JSON 格式
uv run zoc list language_projects --json

# 显示会话详情
uv run zoc show ses_xxx

# 启动 MCP server（供 AI 客户端调用）
uv run zoc mcp
```

### MCP 客户端配置

在 Zed/opencode 的配置文件中添加：

```json
{
  "mcpServers": {
    "zed-opencode-sessions": {
      "command": "uv",
      "args": ["run", "--directory", "C:/WorkingProjects/language_projects/python_projects/zed-opencode-sessions", "zoc", "mcp"]
    }
  }
}
```

### 导出 Markdown

```bash
# 直接查询并导出
uv run python scripts/export_markdown.py ses_xxx -o output.md
```

---

## 跨机器迁移会话

### 场景

将旧电脑上的会话完整迁移到新电脑（新电脑上 Zed 和 OpenCode 是全新安装）。

### 步骤

#### 1. 旧机器：导出会话

```bash
# 导出指定项目目录下的所有会话（不含已归档）
uv run python scripts/export_sessions.py language_projects -o sessions_archive_language_projects.db

# 可选：包含已归档会话
uv run python scripts/export_sessions.py language_projects -o sessions_full.db --archived
```

**输出**：
- 归档文件：`sessions_archive_language_projects.db`（建议命名 `sessions_archive_*.db` 避免被 `.gitignore`）
- 文件大小：根据会话数量，通常 10-100 MB
- 内容：完整的 Zed threads + OpenCode sessions/messages/parts

#### 2. 传输归档文件

将 `sessions_archive_*.db` 文件通过 U盘/网盘/GitHub 传输到新机器。

#### 3. 新机器：预览导入计划（dry-run）

```bash
# 先确保目标目录已存在（例如 D:\Code\my_projects）
mkdir D:\Code\my_projects

# 预览导入（默认行为，不写数据库）
uv run python scripts/import_sessions.py sessions_archive_language_projects.db --target "D:\Code\my_projects"
```

**输出示例**：
```
[0/7] 读取归档文件...
      导出时间: 2026-09-09T02:20:57
      源项目: language_projects
      会话数: 53
      消息数: 2586
      内容数: 10785

[1/7] 检查目标目录... ✓
[2/7] 检查进程... (警告: 检测到 Zed/opencode 正在运行)
[3/7] 定位数据库... ✓
[5/7] 生成 ID 映射...
      53 个 session_id
      2586 个 message_id

[6/7] 预览导入计划 (dry-run)...
      将导入 53 个 thread 到 Zed
      将导入 53 个 session 到 OpenCode
      目标目录: D:\Code\my_projects

✅ Dry-run 完成! 使用 --apply 执行实际导入
```

#### 4. 新机器：实际导入

```bash
# 1. 关闭 Zed 和 opencode（必须！）
# 2. 执行导入
uv run python scripts/import_sessions.py sessions_archive_language_projects.db --target "D:\Code\my_projects" --apply
```

**导入过程**：
- 自动备份 Zed 和 OpenCode 数据库到 `%TEMP%\zoc_backup`
- 重新生成所有 ID（thread_id / session_id / message_id / part_id）
- 创建或复用目标目录的 project 记录
- 重写所有外键关系
- 追加模式（保留新机器上已有的会话）

#### 5. 验证

打开 Zed，切换到目标目录，检查会话是否正常显示。

### 注意事项

1. **目标目录必须提前创建**：导入脚本不会自动创建目录
2. **导入前必须关闭 Zed 和 opencode**：否则导入会失败（进程检测）
3. **追加模式**：导入不会删除新机器上已有的会话，只追加
4. **ID 不可预测**：导入后的 session_id / thread_id 会与旧机器不同（这是设计行为）
5. **自动备份**：每次 `--apply` 导入前会自动备份数据库到临时目录

### 故障排查

**问题：导入后会话不显示**
- 检查目标目录路径是否正确（区分大小写、盘符）
- 确认 Zed 切换到了正确的目录
- 检查 `sidebar_threads.folder_paths` 是否为目标路径

**问题：导入失败，提示进程正在运行**
- 完全退出 Zed 和 opencode（检查任务管理器）
- 确保没有后台残留进程

**问题：归档文件过大**
- 只导出必要的会话（不使用 `--archived`）
- 考虑分批导出（按项目目录分别导出）

---

## 补登 opencode 会话到 Zed

### 场景

直接用 opencode CLI 在某个目录创建的会话，只存在于 `opencode.db`，Zed 从未为其建立 `sidebar_threads` 索引。因此在该目录打开 Zed 时 agent 历史列表看不到这些会话。本脚本把缺失的会话补登进 Zed：**只写 Zed，复用 opencode 原有 session_id，opencode.db 完全不动**，幂等。

### 用法

```bash
# 1. 默认 dry-run 预览（子串匹配；正反斜杠均可）
uv run python scripts/link_sessions.py language_projects

# 2. 传精确目录（子串命中多个目录时用于消歧）
uv run python scripts/link_sessions.py "D:/Users/language_projects"

# 子串命中多个目录时全部处理（每个会话挂到自己的目录）
uv run python scripts/link_sessions.py language_projects --all

# 把所有命中会话强制挂到指定 Zed 工作区
uv run python scripts/link_sessions.py language_projects --target "D:/Users/language_projects"

# 3. 关闭 Zed 和 opencode 后实际写入
uv run python scripts/link_sessions.py language_projects --apply
```

### 行为说明

- **只写 Zed**：不修改 `opencode.db`；已存在索引的会话自动跳过（可反复运行）
- **路径归一化**：opencode 目录用正斜杠、Zed `folder_paths` 用反斜杠，统一 `os.path.normpath`
- **时间格式**：opencode 毫秒时间戳 → Zed 的 UTC ISO（9 位小数 + `+00:00`）
- **子会话**：`parent_id` 非空的 subagent 会话默认跳过（Zed 不为其建独立 thread），`--include-subagents` 可强制纳入
- **多目录**：子串命中多个目录时中止并要求消歧，或 `--all` / `--target`
- **安全**：`--apply` 前检查 Zed 与 opencode 进程均已关闭，自动备份 Zed 三件套到 `%TEMP%\zoc_backup`，写入后 `PRAGMA wal_checkpoint(TRUNCATE)` 并重开只读连接验证
- 写入后需**完全重启 Zed** 再验证历史列表（内存态可能滞后）

---

## 代码结构

- `src/zoc/tools.py`：MCP tool 的唯一实现（`list_sessions` / `get_session_content`），CLI 与 MCP 共用同一批函数
- `src/zoc/mcp_server.py`：MCP 注册与 stdio 传输层，tool 闭包只做委托 + ToolError 翻译
- `src/zoc/cli.py`：CLI 薄壳，直接调用 `tools.py`，负责表格/人类可读渲染
- `src/zoc/core/`：SQLite 访问层（`ZedRepo` / `OpencodeRepo`）与快照管理

## 数据源

- **Zed 数据库**：`%LOCALAPPDATA%\Zed\db\0-stable\db.sqlite`
- **OpenCode 数据库**：`~/.local/share/opencode/opencode.db`

## 技术栈

- Python 3.12+
- Pydantic 2.7+
- MCP SDK 2.1+
- Typer 0.12+
