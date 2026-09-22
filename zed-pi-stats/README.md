# zed-pi-stats

Zed 编辑器中 `pi-acp`（pi coding agent）会话 Token 消耗与成本统计工具。

## 背景与原理

Zed 通过 `pi-acp` 适配器调用 `@earendil-works/pi-coding-agent`。
本工具通过多源关联技术自动统计 Token 消耗与计费：
1. **安全读取 Zed 会话元数据**：复制 `%LOCALAPPDATA%\Zed\db\0-stable\db.sqlite` 临时副本，提取 `sidebar_threads` 中的会话标题与关联工程；
2. **定位底层会话映射**：解析 `~/.pi/pi-acp/session-map.json`，定位对应的会话持久化文件；
3. **逐行解析 Token 日志**：深度解析 `~/.pi/agent/sessions/*.jsonl`，精确提取输入、输出、缓存读写、思考推理 Token 与模型计费，对单会话切换多模型的场景逐 turn 精确归集。

## 运行方式

无需额外安装全局环境，在当前仓库根目录下使用 `uv run` 即可直接执行：

```powershell
# 查看默认统计看板（总览 + 最近10条会话 + 模型汇总 + 工作区汇总）
uv run --project python_projects/zed-pi-stats zed-pi-stats

# 指定展示最近 5 条会话明细
uv run --project python_projects/zed-pi-stats zed-pi-stats -n 5

# 仅查看模型消耗汇总
uv run --project python_projects/zed-pi-stats zed-pi-stats --by-model

# 仅查看工作区工程消耗汇总
uv run --project python_projects/zed-pi-stats zed-pi-stats --by-project

# 仅统计最近 7 天内的会话记录
uv run --project python_projects/zed-pi-stats zed-pi-stats --days 7

# 以 JSON 格式输出（供程序集成或自动化脚本使用）
uv run --project python_projects/zed-pi-stats zed-pi-stats --json

# 导出输出 JSON 的 Schema 规范
uv run --project python_projects/zed-pi-stats zed-pi-stats --schema
```

## 核心特性

- **多模型精确拆分**：如果在一个 Zed 会话中切换过模型（如从 DeepSeek 切换到 Luna/GPT），每条回复按实际调用的模型分别累加到各自模型的统计桶中。
- **安全非侵入**：完全通过 WAL 副本安全只读读取，不产生锁冲突，不影响正在运行的 Zed 与 pi 进程。
- **开箱即用**：零配置自动探测 Zed 数据库与 pi 目录。
