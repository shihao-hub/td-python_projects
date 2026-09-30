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
  API client 与渲染。
- 纯本地命令（`--help`、`--version`、`schema`、补全脚本）不触发 daemon、
  不访问数据库。
- daemon 生命周期：地址文件发现（`--host` → `ZEDHUB_HOST` → 地址文件 →
  默认端口）、生产构建自动拉起（detach + 互斥）、buildID 握手（不等报错
  提示重启）、自动拉起的 daemon 空闲 30 分钟退出；前台 `serve` 不空闲退出。
- 开发构建（源码 / dev 版本运行）不自动拉起，需先手动 `zedhub serve`。

## 数据源边界

当前仅实现 **OpenCode** 数据源（Zed 作为独立索引源）。Pi agent、Claude Code、
Codex、Antigravity 未实现——查询它们返回 `source_not_supported`，不伪造数据。

## 本地数据目录（强约束）

快照、备份、操作 journal、daemon 地址文件/锁/日志只写入
`%APPDATA%\language_projects\zedhub\`（无 APPDATA 时回退
`~/.language_projects/zedhub/`），绝不写入 Zed/OpenCode 数据库所在目录。

## 写操作安全

补登（`sessions link`）与归档导入（`archive import`）默认 dry-run，仅显式
`--apply` 才写库；写入前检查 Zed/OpenCode 进程退出、备份三件套、分库短事务、
checkpoint 与只读复查；跨库部分成功保留 operation journal、ID 映射与备份，
返回 `partial`/`unknown` 状态，绝不虚报成功。

## WebSocket 冻结声明

daemon 同时监听 `127.0.0.1:8765` 的 WebSocket 通道（仅 `threads.list` 与
`stats` 两个只读方法）。该通道是**一次性学习实现**：本轮实现后冻结，不再
新增任何方法或能力；生产与自动化用途一律使用 HTTP API。

## 当前进度

本 README 随实现推进更新；尚未落地的命令会在 `zedhub --help` 中缺失。
设计依据：`docs/projects/python_projects/zedhub/specs/01-zed-session-hub/`。
