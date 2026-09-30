"""MCP 桥：stdio(MCP) ↔ HTTP daemon（v2 §1.2，协议细节按 v1 第 5 章）。

- 桥是 daemon 的客户端：工具实现全部经 HTTP 调用，桥不 import core、
  不直连数据库（NFR-3 / AC-18）。
- 工具元数据来自 contract.MCP_TOOLS（与 schema 导出同源）：
  - 存量平名工具（threads_list 等）保留（FR-9 兼容）；
  - 新增三段式只读工具带 ToolAnnotations + structured_output；
- 写操作不注册（AC-16）；启动先与 daemon 握手，失败即报错退出。
"""

from __future__ import annotations

import inspect
from typing import Any

from .client import DaemonClient
from .contract import HTTP_ENDPOINTS, MCP_TOOLS, ToolMeta, tool_description
from .core.errors import ZedhubError


def _call_http(client: DaemonClient, meta: ToolMeta, params: dict) -> Any:
    """按契约端点表把 MCP 工具调用转为 HTTP（当前 MCP 工具全为 GET 查询）。"""
    path = None
    for ep in HTTP_ENDPOINTS:
        if ep.api_method == meta.api_method and ep.method == "GET":
            path = ep.path
            break
    if path is None:
        raise ZedhubError(f"no HTTP endpoint for tool {meta.name}")
    path_params = {"thread_id", "session_id"}
    for key in path_params:
        if "{" + key + "}" in path:
            path = path.replace("{" + key + "}", str(params.get(key, "")))
    query = {k: v for k, v in params.items() if k not in path_params and v is not None}
    return client.call("GET", path, query=query)


def _tool_error(exc: Exception) -> Exception:
    from mcp.server.mcpserver.exceptions import ToolError

    # ToolError 的 message 会透给客户端；裸异常会被 SDK 当 crash 处理
    return ToolError(f"{type(exc).__name__}: {exc}")


def _build_fn(client: DaemonClient, meta: ToolMeta):
    """构造带显式签名的工具函数（SDK 从签名推导 input/output schema）。"""
    fn_params = [
        inspect.Parameter(
            p.name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=None if not p.required else inspect.Parameter.empty,
        )
        for p in meta.params
    ]
    # 注意 __signature__ 不含 VAR_KEYWORD：SDK 会把它算进 inputSchema
    # structured_output 要求可序列化为 schema 的泛型标注（裸 dict 不行）
    return_ann: type = list[dict[str, object]] if meta.result_type == "array" else dict[str, object]

    def _fn(**kwargs):
        try:
            return _call_http(client, meta, dict(kwargs))
        except ZedhubError as exc:
            raise _tool_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 — 预期外失败也要透出原因
            raise _tool_error(exc) from exc

    # SDK 用 inspect.signature + get_type_hints 推 schema：两者都要满足
    _fn.__name__ = meta.name.replace(".", "_")
    _fn.__doc__ = meta.summary
    _fn.__annotations__ = {p.name: str | None for p in meta.params}
    _fn.__annotations__["return"] = return_ann
    _fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        fn_params, return_annotation=return_ann
    )
    return _fn


def build_server():
    """组装 MCPServer：注册存量平名 + 新三段式只读工具。"""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    client = DaemonClient()
    server = MCPServer(
        "zedhub",
        instructions=(
            "Read-only queries over Zed/OpenCode agent sessions via the local "
            "zedhub daemon (HTTP). Write operations are not exposed over MCP."
        ),
    )

    def register(meta: ToolMeta):
        fn = _build_fn(client, meta)
        if meta.legacy:
            server.tool(description=tool_description(meta))(fn)
        else:
            server.tool(
                name=meta.name,
                description=tool_description(meta),
                annotations=ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                ),
                structured_output=True,
            )(fn)

    for meta in MCP_TOOLS:
        register(meta)
    return server


def serve_mcp() -> None:
    """启动 MCP stdio server；先与 daemon 握手，失败即报错。"""
    import sys

    client = DaemonClient()
    try:
        client.ensure_connected()
    except ZedhubError as exc:
        print(f"zedhub-mcp: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    build_server().run(transport="stdio")
