# todonotify

扫描 `D:\Users\todo` 下的 Markdown 待办文件，汇总未完成任务并推送飞书机器人私聊提醒。

待办文件约定：文件名形如 `<主题>-<YYYYMMDD>.md`，任务用 GFM 复选框标记（`- [ ]` 未完成、`- [x]` 已完成），按 `##` 章节分组；文件内 fenced code block（``` 与 ~~~）会被跳过，代码示例里的行不会误报；正文元信息表中的 `| 预期完成 | YYYY-MM-DD |` 会 best-effort 解析，逾期 / 临近（3 天内）在提醒中标 ⚠️。

## 环境准备

```powershell
cd D:\Users\language_projects\python_projects\todonotify
uv sync
```

依赖仅 typer；飞书发送走现成的 lark-cli（bot 身份，应用凭证长期有效），不引入飞书 SDK。

## 用法

```powershell
uv run todonotify                     # 扫描 + 终端打印未完成清单
uv run todonotify --notify            # 扫描 + 发飞书（定时任务用的就是它）
uv run todonotify --notify --dry-run  # 只打印将执行的 lark-cli 命令与消息体，不实发
uv run todonotify --json              # JSON 输出（结构化，供 agent 分析）
uv run todonotify --dir <path>        # 覆盖默认待办目录 D:\Users\todo
uv run todonotify push --file <md>    # 只发不扫：把 md 当飞书 post 投递（agent 撰写分析用）
uv run todonotify install_schedule    # 注册 09:00 / 20:00 两个每日计划任务
uv run todonotify uninstall_schedule  # 卸载计划任务
```

通知策略：有未完成任务就发全量清单，无未完成静默不发。发送失败退出码为 2。

## 定时任务

当前采用 **DSH 自动化任务**（每天 **09:00 / 20:00**）：到点由 agent 执行 `--json` 拿结构化数据，做优先级分析（逾期 / 今天到期 / 3 天内到期优先），撰写飞书摘要后用 `push --file` 投递；无未完成则静默。这等于把二期规划里的"AI 语义分析"直接落到了调度层，扫描层未改。依赖 DSH 宿主常驻（已配 `HKCU\...\Run` 隐藏自启）。

旧的 Windows 计划任务模式仍保留能力：`install_schedule` 通过 schtasks 注册 `todonotify_morning`（09:00）与 `todonotify_evening`（20:00），`/TR` 指向 venv 内的 `todonotify.exe`；`uninstall_schedule` 幂等卸载。手动触发：

```powershell
schtasks /Run /TN todonotify_morning   # 仅计划任务模式可用
```

注意：删过 `.venv` 后需重新 `uv sync` 并再次执行 `install_schedule`，否则计划任务指向的 exe 已不存在。运行日志在 `%APPDATA%\language_projects\todonotify\todonotify.log`（按天轮转，保留 14 份），无人值守场景的排障看这里。

## 在 pi / opencode 会话中手动触发

让 agent「分析一下我的待办」有两种方式：

1. **拿结构化数据自行分析（推荐）**：

   ```text
   跑 uv run --project D:\Users\language_projects\python_projects\todonotify todonotify --json，
   基于输出的 JSON 帮我总结优先级和风险
   ```

2. **直接读文件**：让 agent 直接读 `D:\Users\todo` 下的 Markdown 文件。

## 二期规划

- 接入本地 qwen 小模型（Ollama，本机已有）做 AI 语义分析：摘要、优先级排序、逾期风险研判，`--notify` 消息升级为 AI 生成的分析报告。
- 一期结构已预留：scanner 产出结构化 `FileReport`，二期在 report 层新增 `render_ai` 渲染器即可，不改扫描层。
