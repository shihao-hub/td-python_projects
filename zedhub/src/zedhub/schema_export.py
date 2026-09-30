"""离线 schema 导出：五通道契约摘要（AC-10，纯本地）。

不连接 daemon、不连接业务数据库——数据源缺失也能完整输出；各段均与
实际注册定义同源（contract 单一事实源），不手抄副本。
"""

from __future__ import annotations

import json
from typing import Any

from . import contract
from .buildid import package_version


def _http_channel() -> dict:
    endpoints = []
    for ep in contract.HTTP_ENDPOINTS:
        endpoints.append(
            {
                "method": ep.method,
                "path": ep.path,
                "summary": ep.summary,
                "api_method": ep.api_method or None,
                "query_params": list(ep.query_params),
                "path_params": list(ep.path_params),
                "body_model": ep.body_model.__name__ if ep.body_model else None,
                "body_schema": (
                    ep.body_model.model_json_schema() if ep.body_model else None
                ),
                "sse": ep.sse,
            }
        )
    return {
        "interface": "http",
        "base_url": "http://127.0.0.1:8766",
        "envelope": {
            "ok": {"ok": True, "data": "...", "meta": {"count": "int?", "elapsed_ms": "int"}},
            "error": {"ok": False, "error": {"code": "str", "message": "str"}},
            "note": "error.code 为唯一稳定契约；HTTP 状态码仅辅助定位",
        },
        "handshake": {
            "header": "X-Zedhub-Build",
            "behavior": "客户端必带；不等返回 409 handshake_mismatch。第三方无头放行",
        },
        "sse": {
            "accept": "text/event-stream（写类端点）",
            "events": {
                "stage": {"fields": ["stage", "detail", "pct?"]},
                "result": "完整结果对象",
                "error": {"fields": ["code", "message"]},
            },
            "disconnect": "断流不中止已开始的写流水线，结果落 operation journal",
        },
        "endpoints": endpoints,
        "query_param_types": contract.QUERY_PARAM_TYPES,
    }


def _mcp_channel() -> dict:
    tools = []
    for t in contract.MCP_TOOLS:
        tools.append(
            {
                "name": t.name,
                "summary": t.summary,
                "params": [
                    {"name": p.name, "description": p.description, "required": p.required}
                    for p in t.params
                ],
                "annotations": (
                    None
                    if t.legacy
                    else {
                        "read_only_hint": True,
                        "destructive_hint": False,
                        "idempotent_hint": True,
                    }
                ),
                "structured_output": not t.legacy,
                "legacy": t.legacy,
                "forwards_to": t.api_method,
            }
        )
    return {
        "interface": "mcp",
        "transport": "stdio（zedhub mcp 桥进程 ↔ HTTP daemon）",
        "start": "zedhub mcp（启动先与 daemon 握手，失败即报错退出）",
        "write_tools": "无（写操作不注册）",
        "tools": tools,
    }


def _ws_channel() -> dict:
    out = dict(contract.WS_PROTOCOL)
    out["default_url"] = "ws://127.0.0.1:8765"
    return out


def _rpc_channel() -> dict:
    return {
        "interface": "json-rpc-2.0",
        "transport": "行分隔 stdio（zedhub rpc 薄壳 ↔ HTTP daemon）",
        "frozen": "方法表冻结，不随新能力扩展（FR-9）",
        "meta_method": {
            "rpc.discover": "纯元数据本地响应，不依赖 daemon（OpenRPC 1.3.2 描述符）"
        },
        "methods": {
            name: {
                "http": f"{m} {p}",
                "summary": contract.RPC_METHOD_SPECS[name]["summary"],
                "params": contract.RPC_METHOD_SPECS[name]["params"],
            }
            for name, (m, p) in contract.RPC_METHODS.items()
        },
        "error_codes": {"-32602": "invalid_params", "-32001": "not_found", "-32000": "server error", "-32601": "method not found"},
    }


def _cli_channel() -> dict:
    return {
        "interface": "cli",
        "pure_local_commands": ["--help", "--version", "schema", "补全脚本生成"],
        "envelopes": {
            "legacy (threads/projects/stats)": '{"status":"ok","data":...,"count":...,"elapsed_ms":...}',
            "new (sessions/stats effort/archive)": '{"ok":true,"data":...} / {"ok":false,"error":{...}}',
        },
        "exit_codes": {"0": "成功（含空结果）", "1": "运行错误", "2": "用法错误"},
        "commands": {
            "serve": "前台 daemon（--host/--port/--ws-port/--db/--opencode-db）",
            "threads list/show": "Zed 线程查询（兼容基线）",
            "projects": "项目统计（兼容基线）",
            "stats": "Zed 总览（兼容基线）",
            "stats effort": "OpenCode 启动模型×档位统计（--watch/-i）",
            "sessions list/show/content": "agent 会话查询（--source/--project/--format/--out）",
            "sessions link": "补登（--all/--target/--include-subagents/--apply）",
            "archive export/inspect/import": "归档导出/检查/导入（-o/--source/--target/--apply）",
            "rpc": "JSON-RPC 兼容薄壳（stdin/stdout）",
            "mcp": "MCP 桥（stdio）",
            "schema": "本导出（--channel）",
        },
    }


def build_schema(channel: str = "all") -> dict[str, Any]:
    builders = {
        "http": _http_channel,
        "mcp": _mcp_channel,
        "ws": _ws_channel,
        "rpc": _rpc_channel,
        "cli": _cli_channel,
    }
    if channel == "all":
        return {
            "name": "zedhub",
            "version": package_version(),
            "channels": {k: fn() for k, fn in builders.items()},
        }
    if channel not in builders:
        raise ValueError(f"unknown channel: {channel!r} (expected one of {list(builders)} or 'all')")
    return {"name": "zedhub", "version": package_version(), "channel": channel, "data": builders[channel]()}


def render(channel: str = "all") -> str:
    return json.dumps(build_schema(channel), ensure_ascii=False, indent=2)
