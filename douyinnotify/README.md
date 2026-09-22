# douyinnotify

监控抖音博主更新并推送飞书提醒的 CLI 工具（Windows 计划任务无人值守模式）。

## 能力表

| 业务用例 | CLI 入口 | MCP 工具 | 说明 |
|---|---|---|---|
| 立即检查一次 | `douyinnotify`（默认） / `check` | `douyinnotify.check` | 抓取 → 差集比对 → 写 state；`--notify` 仅 CLI；MCP 侧不发送 |
| 查看监控状态 | `list` | `douyinnotify.list` | watchlist + 检查间隔 + 上次检查快照 |
| 添加博主 | `add <主页URL\|短链\|sec_uid>` | `douyinnotify.add` | 支持 `www.douyin.com/user/...`、`v.douyin.com` 短链、裸 sec_uid |
| 移除博主 | `remove <sec_uid\|URL>` | `douyinnotify.remove` | 同步清理该博主已见记录 |
| 注册 / 卸载计划任务 | `install_schedule` / `uninstall_schedule` | —（终端管理操作） | 间隔取 `check_interval_hours` |
| 离线契约 | `schema` | — | 与 MCP 实际注册同源的工具目录 JSON |
| 协议入口 | `mcp` | —（自身即 server） | MCP stdio |

通用选项：`--json` 输出 `{"ok": .., "data": ..}` 包络；退出码 `0` 成功（含静默不发）、`2` 参数错误、`1` 其他失败（含发送失败）。

## 快速开始

```powershell
cd D:\Users\language_projects\python_projects\douyinnotify
uv sync                        # 安装依赖并生成 venv 内 exe
uv run douyinnotify            # 手动检查一次（首次运行自动种子默认博主并记基线，不推送）
uv run douyinnotify install_schedule    # 开发/验证完毕后再注册 6 小时定时任务
uv run douyinnotify uninstall_schedule  # 随时卸载
```

`check` 常用组合：

```powershell
uv run douyinnotify check --json              # JSON 包络输出
uv run douyinnotify check --no-save           # 只探测不写 state
uv run douyinnotify check --notify --dry-run  # 打印将执行的 lark-cli 命令与消息体，不实发
```

## 运行方式（Windows 计划任务）

`install_schedule` 注册任务 `douyinnotify_check`，`/SC HOURLY /MO <check_interval_hours> /ST <schedule_start>`（默认 **9 点开始每 6 小时**，即 09:00 / 15:00 / 21:00 / 03:00），指向 venv 内 `pythonw.exe -m douyinnotify --notify`——**pythonw 无控制台，定时触发零弹窗**（stdout 失效由打印容错兜底，排障看日志）。计划任务在全部开发/验证完毕后才注册；`uninstall_schedule` 幂等。

**修改检查频率后需重跑 `install_schedule`** 才对已注册任务生效（schtasks 不感知配置文件变化）。

## 配置与数据目录

所有运行时数据位于 `%APPDATA%\language_projects\douyinnotify\`（无 `APPDATA` 时回退 `~/.language_projects/`）：

| 文件 | 内容 |
|---|---|
| `config.json` | `check_interval_hours`（间隔小时数，默认 6）+ `schedule_start`（每日首次检查时间 HH:MM，默认 09:00）；`watchlist`：sec_uid、昵称、备注 |
| `state.json` | `last_check`；`seen`：每博主已见 aweme_id 集合（新视频判定基准） |
| `chrome_profile\` | 无头 Chrome 专用 profile（持久化 ttwid） |
| `logs\douyinnotify.log` | 按天轮转，保留 14 份 |

首启自动种子「Hucci写代码」；后续用 `add` / `remove` 维护。

## MCP 使用

```powershell
douyinnotify mcp       # stdio server；agent 配置该命令即可
douyinnotify schema    # 导出与注册同源的工具契约 JSON
```

工具：`douyinnotify.check` / `douyinnotify.list` / `douyinnotify.add` / `douyinnotify.remove`。`check` 在 MCP 侧只检查不发送。

## 实现要点

- 抓取：启动系统 Chrome `--headless=new` + 裸 CDP（websocket-client），导航博主主页后**在页面上下文内 `fetch` 作品接口** `/aweme/v1/web/aweme/post/`，取 `aweme_list`（newest-first，含 id / 标题 / 发布时间）。不登录、不点页面任何元素（登录弹窗只是 UI 遮罩，忽略即可）。
  - **为何不用 DOM 锚点**：未登录下页面渲染的作品列表已失效——容器内是空的 `scroll-list` 加“服务异常，重新刷新拉取数据”，页面上仅存的 `/video/` 锚点全在**页脚推荐流**里（随机、每轮互不重叠）。把它当作品会造成“真实更新永远检不出 + 偶尔推送无关视频”（修订背景见 `docs/projects/python_projects/douyinnotify/plans/02-douyin-fetch-api-source.md`）。
  - **为何不用 CDP 读响应体**：页面自身发起的该 XHR，用 `Network.getResponseBody` 取回恒为空 body；改成在页面上下文里 `fetch` 同一接口即可拿到完整 JSON（请求由页面 origin 发出、自带 ttwid 等设备指纹，因此既不需要 `a_bogus` 签名，也不需要登录）。
  - **失败即失败**：HTTP 非 200 / `status_code != 0` / JSON 不可解析 / 缺 `aweme_list` / 列表为空，一律判为抓取失败（不写 state、不推送、记 ERROR 日志），**绝不退回页脚锚点充数**。
- 新视频判定：aweme_id 集合差；每轮把抓到的 id 全量并入 `seen`。
- 发送顺序：先原子写 state 再发消息——保证"发了不重发"，崩溃最多漏发一条。
- Chrome 路径默认扫描 Program Files 常见位置，可用环境变量 `DOUYINNOTIFY_CHROME` 覆盖。

## 已知限制

1. 首次检查只记基线不推送（避免历史视频轰炸）。
2. 单轮新增阈值：单博主单轮新增超过 10 条时只并入已见、不推送，作为**异常放大**的兜底（如 `state.json` 被清空 / 误删）。数据源换成可信的作品接口后已无“抖动”问题，正常更新（通常 1-2 条）不受影响。代价：博主单次连发超过 10 条时会漏推。
3. 先写 state 后发消息：进程在两者之间崩溃会漏发当轮通知（下轮起恢复正常）。
4. 抖音风控可能导致偶发检查失败：失败轮不破坏 state、不推送、记 ERROR 日志，等下轮重试；全部博主都失败时退出码 1（`--json` 包络 `ok:false`）。任何异常都判失败，不会退回页脚锚点充数。
5. CDP 依赖本机 Chrome；删除 venv 后需 `uv sync` 重新生成 exe 并重装计划任务。
6. `install_schedule` 假定 exe 路径无空格（仓库路径约定满足）；改 `check_interval_hours` / `schedule_start` 后需重跑 `install_schedule`。
7. 未登录：博主把作品设为仅登录可见或私密时，接口同样拿不到（表现为抓取失败，不会误报）。
8. 抓取依赖抖音 web 接口（非官方契约）：若接口改版或开始要求签名 / 登录，检查会如实失败（不误报），需按 `plans/02-douyin-fetch-api-source.md` 重新实证。

## 开发

```powershell
uv sync
uv run pyright     # 类型检查（basic 模式，0 错误为发布门槛）
uv run pytest      # 测试
```
