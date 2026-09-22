"""MCP 工具契约目录导出（`schema` 子命令）。

与 mcp_server 实际注册同源：直接从 FastMCP 实例读取工具清单
（name/description/inputSchema/annotations），保证契约即事实。
只 import 业务库、不启动任何业务依赖（无 Chrome、无网络请求）。
"""

from __future__ import annotations

import asyncio
import json


def export_contracts() -> str:
    """导出工具契约目录 JSON。"""
    from .mcp_server import mcp

    async def _collect() -> list[dict]:
        tools = await mcp.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "annotations": tool.model_dump().get("annotations"),
            }
            for tool in tools
        ]

    tools = asyncio.run(_collect())
    payload = {
        "server": "douyinnotify",
        "transport": "stdio",
        "launch": "douyinnotify mcp",
        "tools": tools,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
