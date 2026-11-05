"""Antigravity protobuf 会话（缺 ``transcript.jsonl``）的启发式正文重建。

数据源：``~/.gemini/<variant>/conversations/<sid>.db`` 的 ``steps`` 表
（``step_type`` / ``status`` / ``step_payload``）。``step_payload`` 是无公开
schema 的 protobuf，按任务约定不做完整反序列化，只做 **wire-format 递归扫描**：
逐层取出 length-delimited 字段里的 UTF-8 文本块，按内容特征分类成
user / assistant / tool_call / tool_result / error，再按 ``idx`` 顺序还原时间线。

重建结果必然有损（丢工具结构化参数、可能混入少量元数据噪声），调用方
必须在产物中明示「启发式重建」，不得当作无损正文对外呈现。

设计边界：
- 只读打开 sqlite（``mode=ro``），不写任何文件；
- 解析失败一律降级为空事件，不让单个坏会话拖垮批量导出；
- 过滤规则集中在本模块顶部常量，便于按实测噪声迭代。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .trajectory import TimelineEvent

# 单条文本的最小长度（低于此值的几乎都是字段标签/短标识符）
_MIN_TEXT = 12
# 单步最多保留的候选文本块（防超大会话吃内存）
_MAX_BLOCKS_PER_STEP = 40
# 单条文本上限（与 transcript 解析口径一致）
_MAX_TEXT_LEN = 8000
# protobuf 递归深度上限（正常 payload 不超过 8 层）
_MAX_DEPTH = 12
# 字段号合理上限（超出视为已错位，停止解析）
_MAX_FIELD_NO = 10000

# 噪声前缀：MIME 类型表 / 常见协议常量
_NOISE_PREFIXES = (
    "application/", "text/", "audio/", "video/", "image/",
    "font/", "model/", "multipart/", "message/",
)
# UUID 与工具调用 id：单步里大量出现，非正文
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_NOISE_PATTERNS = (
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
    re.compile(r"^call_\d+$"),
    re.compile(r"^task-\d+$"),
    re.compile(r"^[A-Za-z0-9_+/=]{40,}$"),  # base64 / token
)
# 工具结果正文标记
_TOOL_RESPONSE_RE = re.compile(r"Tool response for ([A-Za-z0-9_.-]+):")
# 工具调用参数特征（出现任一即判定为 tool_call 的 JSON 参数）
_TOOL_ARG_KEYS = (
    '"toolAction"', '"toolSummary"', '"CommandLine"', '"AbsolutePath"',
    '"absolute_path"', '"target_file"', '"ServerName"', '"ToolName"',
)
# 错误正文特征
_ERROR_MARKERS = ("Encountered error", "error executing cascade step", "ERROR_MESSAGE")
# 工具参数特征 → 推断工具名（重建模式没有 name 字段，用于足迹统计与展示）
_TOOL_HINTS: tuple[tuple[str, str], ...] = (
    ('"CommandLine"', "run_command"),
    ('"target_file"', "edit_file"),
    ('"AbsolutePath"', "view_file"),
    ('"absolute_path"', "view_file"),
    ('"DirectoryPath"', "list_dir"),
    ('"SearchDirectory"', "search_files"),
    ('"Url"', "read_url"),
    ('"query"', "search_web"),
    ('"ServerName"', "mcp_tool"),
)
# 控制字符（除换行/制表/回车）：出现即说明该块是嵌套 protobuf，需继续下钻
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 递归下钻后残留在正文前的单字符长度前缀（如 ``k使用…`` / ``Whttps://…``）
_LEN_PREFIX_RE = re.compile(r"^[A-Za-z0-9](?=[\u4e00-\u9fff]|https?://)")


def _is_noise(text: str) -> bool:
    """短标识符 / MIME / UUID / token 等非正文块。"""
    stripped = text.strip()
    cjk = len(re.findall(r"[\u4e00-\u9fff]", stripped))
    if len(stripped) < _MIN_TEXT and cjk < 4:
        return True
    if stripped.startswith(_NOISE_PREFIXES):
        return True
    if any(p.match(stripped) for p in _NOISE_PATTERNS):
        return True
    # UUID 拼接块（如 ``$<sid>"$<sid>``）去掉 UUID 后几乎无正文
    if _UUID_RE.search(stripped):
        return len(_UUID_RE.sub("", stripped).strip(' "$\'\\\n\t')) < 8
    return False


def _read_varint(buf: bytes, i: int) -> tuple[int, int] | None:
    """读一个 varint，返回 (值, 新下标)；越界或超长返回 None。"""
    value = 0
    shift = 0
    n = len(buf)
    while i < n and shift < 64:
        b = buf[i]
        value |= (b & 0x7F) << shift
        shift += 7
        i += 1
        if not b & 0x80:
            return value, i
    return None


def iter_wire_strings(buf: bytes, *, depth: int = 0) -> list[str]:
    """递归扫描 protobuf wire-format，收集可打印的 UTF-8 文本块。

    只处理 wire type 0（varint）、2（length-delimited）、5/1（定长）；
    遇到非法字段号或截断即停止当前层——宁缺毋滥，不猜测错位数据。
    """
    if depth > _MAX_DEPTH:
        return []
    out: list[str] = []
    i = 0
    n = len(buf)
    while i < n:
        head = _read_varint(buf, i)
        if head is None:
            break
        key, i = head
        field_no, wire_type = key >> 3, key & 0x07
        if field_no == 0 or field_no > _MAX_FIELD_NO:
            break
        if wire_type == 0:
            skip = _read_varint(buf, i)
            if skip is None:
                break
            i = skip[1]
        elif wire_type == 2:
            head = _read_varint(buf, i)
            if head is None:
                break
            length, i = head
            end = i + length
            if length < 0 or end > n:
                break
            chunk = buf[i:end]
            i = end
            try:
                text = chunk.decode("utf-8")
            except UnicodeDecodeError:
                out.extend(iter_wire_strings(chunk, depth=depth + 1))
                continue
            printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\t")
            if text and printable / len(text) > 0.98 and not _CTRL_RE.search(text):
                out.append(text)
            else:
                out.extend(iter_wire_strings(chunk, depth=depth + 1))
        elif wire_type == 5:
            i += 4
        elif wire_type == 1:
            i += 8
        else:
            break
    return out


def _natural_blocks(texts: list[str]) -> list[str]:
    """过滤噪声、去重，保留自然语言 / JSON 参数候选（保持原顺序）。"""
    seen: set[str] = set()
    blocks: list[str] = []
    for raw in texts:
        text = _CTRL_RE.sub("", raw).strip()
        text = _LEN_PREFIX_RE.sub("", text)
        if _is_noise(text) or text in seen:
            continue
        seen.add(text)
        blocks.append(text)
        if len(blocks) >= _MAX_BLOCKS_PER_STEP:
            break
    return blocks


def _tool_name_from_result(text: str) -> str:
    m = _TOOL_RESPONSE_RE.search(text)
    return m.group(1) if m else "tool_result"


def _has_tool_args(text: str) -> bool:
    """JSON 参数特征判定（不要求以 ``{`` 开头：重建文本可能带前缀）。"""
    return any(k in text for k in _TOOL_ARG_KEYS)


def _infer_tool_name(text: str) -> str:
    """从参数 JSON 的字段特征推断工具名（重建模式专用）。"""
    for marker, name in _TOOL_HINTS:
        if marker in text:
            return name
    return "tool"


def _pick_longest(blocks: list[str]) -> str:
    return max(blocks, key=len) if blocks else ""


def _pick_body(blocks: list[str]) -> str:
    """挑最像正文的块：中文与自然语言加权，JSON/XML 片段降权。"""
    def score(text: str) -> float:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        words = len(text.split())
        json_like = text.lstrip().startswith(("{", "[", "<"))
        return cjk * 8 + words * 2 + len(text) * 0.1 - (300 if json_like else 0)

    return max(blocks, key=score) if blocks else ""


def _step_event(idx: int, step_type: int, status: str, ts: str,
                blocks: list[str]) -> TimelineEvent | None:
    """单步候选文本 → 一个 TimelineEvent（无法判定时返回 None）。"""
    if not blocks:
        return None

    joined = "\n".join(blocks)
    # 1) 工具结果回声：优先取 "Tool response for" 之后的内容
    if _TOOL_RESPONSE_RE.search(joined):
        m = _TOOL_RESPONSE_RE.search(joined)
        body = joined[m.end():].strip() if m else joined
        return TimelineEvent(
            role="tool_result", name=_tool_name_from_result(joined),
            status=status, text=(body or joined)[:_MAX_TEXT_LEN], ts=ts,
        )
    # 2) 工具调用参数 JSON
    tool_json = next((b for b in blocks if _has_tool_args(b)), None)
    if tool_json is not None:
        return TimelineEvent(
            role="tool_call", name=_infer_tool_name(tool_json), status=status,
            text=tool_json[:_MAX_TEXT_LEN], ts=ts,
        )
    # 3) 错误
    if any(marker in joined for marker in _ERROR_MARKERS):
        return TimelineEvent(
            role="error", status=status,
            text=_pick_body(blocks)[:_MAX_TEXT_LEN], ts=ts,
        )
    # 4) step_type 14 为用户输入（agy 的 USER_INPUT），其余为助手正文
    role = "user" if step_type == 14 else "assistant"
    return TimelineEvent(role=role, status=status,
                         text=_pick_body(blocks)[:_MAX_TEXT_LEN], ts=ts)


def _row_timestamp(metadata: object) -> str:
    """从 metadata protobuf 里尽力取一个 ISO 时间戳（取不到返回空串）。"""
    if not isinstance(metadata, (bytes, bytearray)):
        return ""
    for text in iter_wire_strings(bytes(metadata)):
        m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text)
        if m:
            return m.group(0)
    return ""


def parse_agy_step_db(db_path: Path, *, limit: int = 2000) -> list[TimelineEvent]:
    """读取 ``conversations/<sid>.db`` 的 steps，重建时间线事件。

    只读打开；表结构缺失、payload 非 bytes、单步解析异常都安静跳过。
    返回事件按 ``idx`` 升序，首条非空 step 判为用户输入。
    """
    events: list[TimelineEvent] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return events
    try:
        rows = con.execute(
            "SELECT idx, step_type, status, metadata, step_payload"
            "  FROM steps ORDER BY idx"
        ).fetchall()
    except sqlite3.Error:
        con.close()
        return events
    try:
        for idx, step_type, status, metadata, payload in rows:
            if not isinstance(payload, (bytes, bytearray)):
                continue
            blocks = _natural_blocks(iter_wire_strings(bytes(payload)))
            ts = _row_timestamp(metadata)
            event = _step_event(int(idx), int(step_type or 0), str(status or ""), ts, blocks)
            if event is None:
                continue
            # 相邻重复（同一工具参数在相邻 step 重复出现）只保留一条
            if events and events[-1].role == event.role and events[-1].text == event.text:
                continue
            events.append(event)
            if len(events) >= limit:
                break
    finally:
        con.close()
    return events


def locate_step_db(conversation_id: str, *, home: Path | None = None) -> Path | None:
    """在四大变体实例中定位 ``conversations/<sid>.db``。"""
    from .agent_paths import ANTIGRAVITY_VARIANTS, agent_data_root

    root = agent_data_root("antigravity", home=home)
    for variant in ANTIGRAVITY_VARIANTS:
        candidate = root / variant / "conversations" / f"{conversation_id}.db"
        if candidate.is_file():
            return candidate
    return None


def load_agy_events(conversation_id: str, *, home: Path | None = None,
                    limit: int = 2000) -> tuple[list[TimelineEvent], bool]:
    """会话正文统一入口：transcript 优先，缺失时回退 steps.db 重建。

    返回 ``(events, reconstructed)``；两者都没有时返回 ``([], False)``。
    """
    from .agy_extractor import _brain_transcript_path
    from .trajectory import parse_antigravity_transcript

    transcript = _brain_transcript_path(conversation_id, home=home)
    if transcript is not None:
        return parse_antigravity_transcript(transcript, limit=limit), False
    db_path = locate_step_db(conversation_id, home=home)
    if db_path is not None:
        return parse_agy_step_db(db_path, limit=limit), True
    return [], False


__all__ = [
    "iter_wire_strings",
    "load_agy_events",
    "locate_step_db",
    "parse_agy_step_db",
]
