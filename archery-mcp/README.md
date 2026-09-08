# archery-mcp

Archery SQL 平台查询工具（单模块 `archery_sql_mcp/__init__.py`）：自动登录 + 会话自愈 + 只读查询。
无参数启动为 MCP stdio server，带子命令则为 CLI。

## 使用

```bash
uv sync
uv run archery-sql-mcp --version
uv run archery-sql-mcp check            # 登录 + SELECT 1 全链路验证
uv run archery-sql-mcp query "SELECT 1"
uv run archery-sql-mcp redis "scan 0 match xxx* count 100"
uv run archery-sql-mcp                  # MCP stdio server 模式
```

配置复用 `~/.archery-mcp/config.json`（首次可用 `uv run archery-sql-mcp init` 初始化）。
