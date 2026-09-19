# zedhub

读取 Zed 编辑器本地 SQLite 的只读查询 CLI：检索 / 统计 Zed 的 agent 会话记录。
领域知识、架构与开发规约见 [HANDOFF.md](HANDOFF.md)。

## 程序化对接（标准协议，无需了解私有信封）

- `zedhub rpc`：JSON-RPC 2.0（line-delimited stdio），供普通程序/脚本调用
- `zedhub mcp`：MCP stdio server，供 AI 客户端（Zed / Claude Code / opencode）接入

方法表、错误码与各客户端接入配置见父仓库
`docs/projects/python_projects/zedhub/zedhub 对接协议.md`。

## 运行

```powershell
uv run zedhub --help    # 直接跑（uv 自动 sync 依赖）
.\zedhub.exe stats      # launcher 壳，等价于上一条
```

## 出 exe

exe 本体是 python-launcher-go（`go_projects/pythonlauncher`）编译的通用启动器壳（拉起
`uv run --project <本项目> -- zedhub <参数>`），按命令名改名部署到本项目根：

```powershell
..\python-launcher-go\build.ps1 zedhub    # 编译 launcher.exe 并部署为 .\zedhub.exe
```

前提：机器上有 uv（在 PATH 或 `%USERPROFILE%\.local\bin`）；本机换路径/换机器时重新部署一次即可。
