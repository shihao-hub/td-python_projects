# 下一场（Next Scene）

帮已经写了一部分故事、却卡在"接下来写什么"的中文小说作者，把一个卡点推进成一场可以继续加工的戏。

## 解决的问题

目标用户：正在写小说/短篇/剧本、手里有前文但对下一步没有判断的作者。

核心判断：卡文时作者缺的不是"一段文字"，而是**下一步的方向选择**。直接让 AI 续写，作者只能接受或重掷；先让 AI 给出几个有实质区别的发展方向，由作者选择并调整，再展开成正文，创作决策权留在作者手里。

## 核心路径（一条完整走通的用户路径）

1. 粘贴前文 + 一句话卡点（可加"必须保留的设定"，AI 不会违背）
2. AI 生成 3 个发展方向，每个包含：人物接下来的具体行动 / 如何承接前文 / 会带来的后果
3. 选择一个方向，可补充调整意见（如"对白再克制一点"）
4. 生成约 600–1000 字的下一场戏
5. 正文可编辑、复制、下载 TXT；内容自动保存在浏览器会话中，刷新不丢；页头「重新开始」一键清空全部工作内容

内置悬疑 / 日常 / 幻想三份原创示例，可一键填入直接体验。页头右侧有一个实验性的「当前连接」数字卡，实时显示当前打开的客户端页面数（WebSocket 连接数，每 2 秒自动刷新，由主进程 ASGI 中间件统计）。

## 技术栈

- Python + [NiceGUI](https://nicegui.io) 3.16（纯 Python Web UI）
- httpx 异步调用 OpenAI 兼容的 Chat Completions 接口（默认智谱 GLM）
- 无数据库：工作内容通过 NiceGUI 的浏览器会话存储（`app.storage.user`）持久化到 `%APPDATA%\language_projects\next-scene\`（取不到 APPDATA 回退 `~/.language_projects/next-scene/`）
- 模型接入集中在 `llm.py`（两个函数：`suggest_directions` / `write_scene`），UI 不感知服务商

```
src/next_scene/
    main.py      NiceGUI 界面与交互
    llm.py       提示词、模型调用、JSON 解析与重试
    samples.py   三份原创示例
tests/           解析层单元测试
scripts/         真实链路冒烟与问题诊断脚本
```

## 快速开始

```powershell
uv sync
Copy-Item .env.example .env    # 填入你的 LLM_API_KEY
uv run next-scene              # 打开 http://127.0.0.1:8080
```

或使用 pip：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt .
.venv\Scripts\next-scene
```

### 双击启动（next-scene.exe）

项目根目录的 `next-scene.exe` 是通用 uv 启动器（`go_projects/python-launcher-go` 构建）：exe 文件名对应 `[project.scripts]` 的命令名，运行等价于 `uv run next-scene`。前提是 PATH 里有 uv（或 uv 位于 `%USERPROFILE%\.local\bin`）；首次运行会自动 sync 依赖。exe 属本地部署件（`.gitignore` 已排除 `*.exe`）。

## 环境变量

| 变量 | 必填 | 说明 | 默认值 |
|---|---|---|---|
| LLM_API_KEY | 是 | OpenAI 兼容服务的 API Key | 无 |
| LLM_BASE_URL | 否 | API 地址 | `https://open.bigmodel.cn/api/coding/paas/v4` |
| LLM_MODEL | 否 | 模型名 | `glm-5.3` |
| LLM_THINKING | 否 | GLM 思考模式（见下文"发现的问题"） | 不发送该参数 |
| STORAGE_SECRET | 否 | 浏览器存储签名密钥，部署时务必修改 | 开发默认值 |
| PORT / NEXT_SCENE_SHOW | 否 | 监听端口 / 是否自动打开浏览器 | `8080` / `1` |

`.env` 文件会被自动加载（仅本地调试用，已列入 `.gitignore`，不要提交真实密钥）。

## 部署（Docker）

```bash
docker build -t next-scene .
docker run -d --restart always -p 80:8080 \
  -e LLM_API_KEY=your-api-key \
  -e LLM_THINKING=disabled \
  -e STORAGE_SECRET=$(openssl rand -hex 16) \
  next-scene
```

## 测试与验证

- **单元测试**：`uv run pytest`（11 个用例，覆盖模型输出的 JSON 提取——围栏代码块、前后缀文本、嵌套大括号、非法输入——以及方向结构校验与截断）
- **真实链路冒烟**：`uv run python scripts/smoke_llm.py`，用悬疑示例走完"3 方向 → 正文"
- **服务冒烟**：`/health` 返回 `{"ok": true}`，`/client-count` 返回当前 WebSocket 连接数，首页 SSR 渲染包含完整三步结构
- **手工验收项**：三种文体示例分别生成，检查方向差异与承接；空输入、超长输入（>5000 字截断）、模型超时/坏 JSON（方向解析失败自动重试一次）、生成期间按钮禁用防重复提交；两个浏览器会话内容隔离；刷新与服务重启后工作内容恢复

## 当前边界与取舍（如实说明）

真实可用：方向生成、正文生成、调整意见、编辑/复制/下载、会话保存，均为真实模型调用，无 mock。

未完成 / 不做（首版范围外）：

- 未部署公网（当前仅本地运行），部署脚本已就绪
- 单故事工作区：无账号体系、无多作品管理、无历史版本
- 非流式输出：正文整段返回，长文需等待约 10–30 秒（有等待反馈）
- 方向数量固定 3 个，不支持追加或局部重生成
- 内容安全与事实性依赖模型本身，未加额外审核层

继续做的第一优先级：流式输出正文 + 方向卡片"换个思路"局部重生成。

## AI 参与开发

全程使用 AI（opencode + GLM-5.3）完成：解读题目、产品定义与范围收敛、技术选型、全部代码与测试、调试与验证脚本。人对最终代码负责，以下是一次典型的问题闭环：

- **AI 输出问题 1（模型侧，已修复）**：首版真实调用时正文偶发返回空内容。通过 `scripts/debug_scene.py` 三组对照实验定位根因：GLM-5.3 是思考型模型，思考过程消耗 `max_tokens` 预算，思考过长时 `finish_reason=length` 且 `content` 为空（曾观测到一次思考 2.4 万字耗尽 8192 预算）。修复：`LLM_THINKING=disabled` 关闭思考（创作任务不需要长推理，速度和稳定性显著提升），并在 `llm.py` 显式识别截断错误、给出可操作的提示。
- **AI 输出问题 2（框架侧，已修复）**：AI 生成的界面代码使用了 `Textarea.on_change()` 方法，NiceGUI 3.x 中该元素没有此方法，导致页面 500。HTTP 冒烟暴露后改为构造参数 `on_change=`，复测通过。

## 实际投入时间

约 2.5 小时（含选题讨论、实现、验证；录屏与提交材料另计）。
