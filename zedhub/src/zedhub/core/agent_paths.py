"""三 agent 源（claude-code/codex/antigravity）的本地数据文件定位（FR-7 扩展）。

- 仅服务 archive export/import 的**整文件字节搬运**：不做逐行解析、不解析
  antigravity 的 protobuf step_payload（无公开 schema）；
- session_id 均以 Zed sidebar_threads 为锚（UUID），映射到各源用户级数据根：
  - claude-code：``~/.claude/projects/<slug>/<sid>.jsonl``（文件名即 sid）；
  - codex：``~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl``；
  - antigravity：``~/.gemini/antigravity-acp/conversations/<sid>.db``（每会话
    一个 SQLite，旁有同主名 ``.meta`` 记录 cwd/mode，一并搬运）；
- 数据根默认取用户 home，``home`` 形参可覆盖（测试/隔离验证用）。
"""

from __future__ import annotations

from pathlib import Path

from .errors import SourceNotSupportedError

# source id → Zed sidebar_threads.agent_id（以 Zed threads 为锚的索引键）
SOURCE_AGENT_IDS: dict[str, str] = {
    "opencode": "opencode",
    "claude-code": "claude-acp",
    "codex": "codex-acp",
    "antigravity": "antigravity-acp",
    "pi": "pi-acp",
    "pi-acp": "pi-acp",
}

# 归档 schema v2 覆盖的文件级源（opencode 走 v1 专用路径，不经本模块）
FILE_SOURCES: tuple[str, ...] = ("claude-code", "codex", "antigravity", "pi")

# 各源数据根目录名（相对用户 home）
_DATA_ROOT_NAMES: dict[str, str] = {
    "claude-code": ".claude",
    "codex": ".codex",
    "antigravity": ".gemini",
    "pi": ".pi",
}


def agent_id_for(source: str) -> str:
    """source id → Zed agent_id；未知 source 抛 SourceNotSupportedError。"""
    agent_id = SOURCE_AGENT_IDS.get(source)
    if agent_id is None:
        raise SourceNotSupportedError(
            f"数据源未支持: {source}；已支持: opencode, "
            + ", ".join(FILE_SOURCES)
        )
    return agent_id


def agent_data_root(source: str, *, home: Path | None = None) -> Path:
    """各源用户级数据根：``~/.claude`` | ``~/.codex`` | ``~/.gemini``。"""
    name = _DATA_ROOT_NAMES.get(source)
    if name is None:
        raise SourceNotSupportedError(
            f"数据源未支持: {source}；已支持: opencode, " + ", ".join(FILE_SOURCES)
        )
    return (home or Path.home()) / name


def locate_session_files(
    source: str,
    session_ids: list[str],
    *,
    home: Path | None = None,
) -> dict[str, list[Path]]:
    """定位一批 session_id 对应的本地数据文件；同 sid 多文件全收。

    返回 ``{sid: [文件, ...]}``；未命中的 sid 不出现在结果里（调用方据此
    统计 missing）。目录不存在时安静返回空 dict（如实按 missing 报告）。
    """
    if isinstance(session_ids, str):
        session_ids = [session_ids]
    wanted = set(session_ids)
    found: dict[str, list[Path]] = {}
    if not wanted:
        return found
    root = agent_data_root(source, home=home)

    if source == "claude-code":
        base = root / "projects"
        if base.is_dir():
            for f in base.glob("*/*.jsonl"):
                if f.stem in wanted:
                    found.setdefault(f.stem, []).append(f)
    elif source == "codex":
        base = root / "sessions"
        if base.is_dir():
            for f in base.rglob("*.jsonl"):
                for sid in wanted:
                    if f.name.endswith(f"-{sid}.jsonl"):
                        found.setdefault(sid, []).append(f)
    elif source == "antigravity":
        base = root / "antigravity-acp" / "conversations"
        if base.is_dir():
            for sid in wanted:
                for name in (f"{sid}.db", f"{sid}.meta"):
                    f = base / name
                    if f.is_file():
                        found.setdefault(sid, []).append(f)
    elif source in ("pi", "pi-acp"):
        base = root / "agent" / "sessions"
        if base.is_dir():
            for sid in wanted:
                for f in base.glob(f"*/*{sid}*.jsonl"):
                    if f.is_file():
                        found.setdefault(sid, []).append(f)
    else:
        raise SourceNotSupportedError(
            f"数据源未支持: {source}；已支持: opencode, " + ", ".join(FILE_SOURCES)
        )
    return found
