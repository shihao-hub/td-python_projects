"""zedhub — Zed + OpenCode 会话统一工具（daemon 架构，v2 标准试点）。

架构：`zedhub serve` 是唯一业务进程（daemon），对本地回环提供 HTTP+JSON
API（唯一契约）；CLI、MCP 桥、兼容 JSON-RPC 均为 daemon 的薄客户端；
WebSocket 为一次性学习实现（冻结）。详见 README 与对接协议文档。
"""
