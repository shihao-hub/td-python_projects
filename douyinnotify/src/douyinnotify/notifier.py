"""subprocess 调用 lark-cli 发送飞书机器人私聊消息。

不引入飞书 SDK，直接复用现成 CLI（移植自 todo_notify）：
lark-cli im +messages-send --as bot --user-id <open_id> --msg-type post --content <post JSON>

用 bot 身份而非 user：user token 会过期，bot 是应用凭证，适合无人值守定时任务。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

logger = logging.getLogger("douyinnotify")

# 收件人 open_id（张世豪）
RECIPIENT_OPEN_ID = "ou_7ad1912240ad66760cb0efa360eb8711"


class NotifyError(RuntimeError):
    """飞书发送失败。"""


def _resolve_lark_cli() -> str:
    """解析 lark-cli 完整路径。

    lark-cli 是 npm shim（lark-cli.cmd），Windows CreateProcess 不会自动
    搜索 PATHEXT 后缀，必须用 shutil.which 解析到 .cmd 才能启动。
    """
    exe = shutil.which("lark-cli")
    if exe is None:
        raise NotifyError("未找到 lark-cli，请确认已安装且在 PATH 中")
    return exe


def build_post_content(markdown: str, title: str | None = None) -> str:
    """把 markdown 包装为飞书 post 内容 JSON（单行字符串）。

    不能用 --markdown 直传多行文本：lark-cli 是 npm shim，参数经 cmd.exe
    中转时真实换行符会被当作命令分隔符截断，只有首行能送达。
    改用 --content 传 JSON：换行在 JSON 里是转义序列 \\n（两个字符），
    命令行上始终是单行字符串。ensure_ascii=True 进一步保证纯 ASCII 传参。
    首行（标题）用作 post 标题，手机通知预览显示它。
    """
    lines = markdown.splitlines()
    resolved_title = title or (lines[0].strip() if lines else "抖音更新提醒")
    payload = {
        "zh_cn": {"title": resolved_title, "content": [[{"tag": "md", "text": markdown}]]}
    }
    return json.dumps(payload, ensure_ascii=True)


def build_command(markdown: str, title: str | None = None) -> list[str]:
    """构造 lark-cli 发送命令（dry-run 展示与实发共用）。"""
    return [
        _resolve_lark_cli(),
        "im",
        "+messages-send",
        "--as",
        "bot",
        "--user-id",
        RECIPIENT_OPEN_ID,
        "--msg-type",
        "post",
        "--content",
        build_post_content(markdown, title),
    ]


def send_lark(markdown: str, title: str | None = None) -> str:
    """发送 markdown 消息，成功返回 message_id，失败抛 NotifyError。

    从 Python 以列表参数经 CreateProcess 调用 lark-cli，
    不经过 Git Bash 的 MSYS 路径转义，无引号歧义问题。
    """
    cmd = build_command(markdown, title)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,  # lark-cli.cmd 的 cmd.exe 中转不闪窗
        )
    except OSError as exc:
        logger.error("启动 lark-cli 失败: %s", exc)
        raise NotifyError(f"启动 lark-cli 失败: {exc}") from exc
    if result.returncode != 0:
        logger.error("lark-cli 退出码 %s，stderr: %s", result.returncode, result.stderr)
        raise NotifyError(f"lark-cli 退出码 {result.returncode}: {result.stderr.strip()}")

    # 成功响应 stdout 是含 message_id 的 JSON
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.error("lark-cli stdout 非 JSON: %s", result.stdout)
        raise NotifyError("lark-cli 返回内容无法解析为 JSON") from None

    message_id = payload.get("message_id") or payload.get("data", {}).get("message_id")
    if not message_id:
        logger.error("lark-cli 响应缺少 message_id: %s", result.stdout)
        raise NotifyError(f"lark-cli 响应缺少 message_id: {result.stdout.strip()}")
    return str(message_id)
