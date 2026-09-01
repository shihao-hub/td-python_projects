# python-launcher-go

通用 uv 项目启动器：一个 Go 编写的小 exe，拷进任何 uv 管理的 Python 项目、按规则改名后，执行它就等于执行 `uv run`，参数与退出码原样透传，调用体验与直接运行命令完全一致。

## 工作原理

启动器自身零输出，实际执行的是：

```
uv run --project <项目目录> -- <命令名> <exe 收到的所有参数>
```

- **命令名 = exe 文件名**（去掉 `.exe`）：`zedhub.exe` → 执行 `zedhub`
- **项目目录**：从 exe 所在目录向上找最近的 `pyproject.toml`（可放项目根或任意子目录）
- **uv 定位**：先找 PATH，兜底 `%USERPROFILE%\.local\bin\uv.exe`
- **无感知**：stdio 直接继承当前控制台；子进程退出码原样返回；调用者 cwd 保持不变，相对路径参数（如 `--db`、`--out`）按调用处目录解析

## 使用方法

```powershell
# 1. 编译通用件（产出 python-launcher-go\launcher.exe）
.\build.ps1

# 2a. 一键部署：拷贝并改名到兄弟项目目录（..\zedhub\zedhub.exe）
.\build.ps1 zedhub

# 2b. 或手工部署：把 launcher.exe 拷到目标项目根目录，改名为命令名
```

运行示例（在任意 cwd 下均可）：

```
zedhub.exe stats
zedhub.exe threads list --table --limit 5
```

## 命名规则（重要）

exe 名必须等于目标项目 `[project.scripts]` 里的**命令名**，不是项目名——只是惯例上两者一致。

```toml
[project.scripts]
mt = "my_tool.cli:main"   # 项目叫 my-tool，但命令叫 mt → exe 必须叫 mt.exe
```

同一个项目有多个 scripts 入口时，可拷多份 exe 分别命名，各自拉起不同入口。

## 注意事项

- 前提：目标项目用 uv 管理（有 `pyproject.toml`），且 `[project.scripts]` 有同名入口；否则 uv 会报 command not found
- exe 必须放在目标项目树内（向上找不到 `pyproject.toml` 时启动器报错退出 1）
- 首次运行或依赖变更时 uv 会自动 sync，可能有安装输出，属 uv 本身行为
- 用户参数放在 `--` 之后，不会被 uv 误解析（zedhub 的 `--project` 与 uv 的 `--project` 不冲突）
- 退出码透传约定：0 成功 / 1 运行错误 / 2 用法错误，脚本可放心判断
- 换机器只需 PATH 里有 uv（或位于 `%USERPROFILE%\.local\bin`），启动器本身无任何运行时依赖
- 根 `.gitignore` 已配置 `*.exe`，各项目的改名 exe 均不会入库

## 已验证（2026-09，zedhub + file-sync-py）

- `stats` / `threads list --table`：JSON 与表格输出正常，30ms 级开销
- `threads list --project go_projects`：参数透传不撞 uv 同名 flag
- `threads show <不存在id>` → 退出码 1；`badcmd` → 退出码 2
- 从 Temp 目录调用：cwd 保持调用者目录，相对路径行为不变
- 通用性：拷贝改名到 file-sync-py，正确拉起其入口并透传退出码
- `gofmt` / `go vet` 通过
