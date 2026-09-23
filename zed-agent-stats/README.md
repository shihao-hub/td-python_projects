# zed-agent-stats

Zed 编辑器中 ACP 智能体（**pi / antigravity / opencode**）会话 Token 消耗、交互轮次与预估费用的轻量统计工具。

---

## ✨ 核心特性

- **多智能体子命令**：`pi` / `antigravity`（缩写 `agy`）/ `opencode`（缩写 `oc`），不带子命令即三智能体合并总览（含 by-agent 分解）。
- **三位一体契约对齐**：
  - **人读终端视图**：自适应弹性列排版（`expand=True`），垂直对齐，防文字截断。
  - **机器结构化 JSON**：`--json` 输出与查询参数（`--by-model` / `--by-project` / `--by-session`）精准对齐的精简 JSON。
  - **动态契约反射 Schema**：`--schema` 零 I/O 毫秒级输出当前视图专有的 JSON Schema，方便上游下游（如 Agent / CI 脚本）进行确定性校验与代码生成。
- **混合模型自动折叠与分解**：同一个会话中切过模型，按模型分别统计，并汇总全会话。
- **本地数据安全读取**：Zed db / antigravity 会话库采用快照复制防锁；opencode 大库（GB 级）以只读 URI 直查 + 短重试，绝不复制。
- **轻量依赖**：仅 Rich（终端渲染）与 blackboxprotobuf（无 .proto 的 protobuf 解码）。

---

## 🚀 常用命令

### 1. 合并总览（全部智能体）

```powershell
# 总览看板 (各智能体分解 + 最近会话 + 模型汇总 + 工作区汇总)
zed-agent-stats
```

### 2. 单智能体看板

```powershell
# pi 智能体
zed-agent-stats pi

# antigravity（缩写 agy）
zed-agent-stats antigravity
zed-agent-stats agy --by-model

# opencode（缩写 oc）
zed-agent-stats opencode --by-session -n 20
zed-agent-stats oc --by-model
```

### 3. 投影与机器自动化（JSON + Schema 对齐）

```powershell
# 模型维度 JSON / Schema
zed-agent-stats agy --by-model --json
zed-agent-stats oc --by-model --schema

# 会话明细 JSON（最近 5 条）
zed-agent-stats pi --by-session -n 5 --json

# 全量报告（含 by_agent / by_model / by_project / sessions）
zed-agent-stats --json
zed-agent-stats --schema
```

### 4. 展示选项

```powershell
# 展开全部列 (宽终端) + 显示未缩写的原始大整数
zed-agent-stats --wide --raw

# 仅统计最近 7 天
zed-agent-stats agy --days 7
```

> 参数请放在子命令之后（如 `zed-agent-stats pi --by-model`）。

---

## 📦 安装与运行

```powershell
# 在本项目目录下直接运行
uv run zed-agent-stats

# 或者全局安装到 uv 工具箱
uv tool install .
```

---

## 🔌 数据源说明

| 智能体 | 会话数据 | Token 来源 | Cost |
|---|---|---|---|
| `pi` | `~/.pi/pi-acp/session-map.json` → 会话 JSONL | 逐条 `message.usage` | JSONL 内置估算费用 |
| `antigravity` | `~/.gemini/antigravity-acp/conversations/<sid>.db`（每会话一个 SQLite，`gen_metadata` 表 protobuf blob） | protobuf 字段 `1.4`：input/output/reasoning | 无（订阅制，恒为 0） |
| `opencode` | `~/.local/share/opencode/opencode.db` 的 `session` 表聚合列 + `message.data` 细分 | 聚合列 tokens_input/output/reasoning/cache_read/cache_write | cost 字段（订阅 plan 常为 0） |

三智能体共用 Zed 层：`%LOCALAPPDATA%\Zed\db\0-stable\db.sqlite` 的 `sidebar_threads`（按 `agent_id` 过滤，快照复制防锁），提供标题 / 工作区 / 时间戳，`session_id` 与各智能体自身存储主键直接相等。

覆盖范围说明：

- `opencode` 子命令覆盖 opencode.db 中**全部**会话（含 Zed 内与独立终端运行的 opencode）。
- antigravity 的 `reasoning_tokens` 为 thinking tokens（通常是大头）；其 usage 中另有两个语义未定的小字段（f9/f10），当前保守忽略、不计入 total，待校准。
- 费用为 0 时终端表格显示 `-`（订阅制不回传费用），JSON 保留数值 0。
