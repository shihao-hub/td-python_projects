"""operation journal：跨库写操作的可恢复证据（设计 §11）。

状态机（固定）：``planned → opencode_committed → zed_committed → verified``
或任一阶段失败后的 ``partial`` / ``unknown``。

v2 归档导入（文件级源）走 ``planned → files_committed → zed_committed →
verified``：不写 OpenCode，阶段 1 是源数据文件落盘。

- 只记录恢复所需的最小信息：操作状态、参数摘要、备份位置、ID 映射与
  已提交阶段；不保存会话正文副本。
- journal 在验证完成后保留（人工/专用恢复动作使用），原子写防止半截 JSON。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import DataDirError
from .paths import data_subdir

STATUS_PLANNED = "planned"
STATUS_OPENCODE_COMMITTED = "opencode_committed"
STATUS_FILES_COMMITTED = "files_committed"
STATUS_ZED_COMMITTED = "zed_committed"
STATUS_VERIFIED = "verified"
STATUS_PARTIAL = "partial"
STATUS_UNKNOWN = "unknown"


@dataclass
class OperationRecord:
    operation_id: str
    kind: str                      # "sessions.link" | "archive.import" | ...
    status: str = STATUS_PLANNED
    params: dict = field(default_factory=dict)
    backup_dirs: list[str] = field(default_factory=list)
    committed_stages: list[str] = field(default_factory=list)
    id_maps: dict = field(default_factory=dict)   # {"session": {old: new}, ...}
    counts: dict = field(default_factory=dict)    # 计划/实际写入数量
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_payload(self) -> dict:
        return asdict(self)

    def set_status(self, status: str) -> None:
        self.status = status
        if status in (STATUS_OPENCODE_COMMITTED, STATUS_FILES_COMMITTED, STATUS_ZED_COMMITTED):
            self.committed_stages.append(status)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


def journal_file(operation_id: str) -> Path:
    return data_subdir("operations") / f"{operation_id}.json"


def save_operation(record: OperationRecord) -> None:
    """原子写 journal（临时文件 + os.replace）。"""
    record.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    f = journal_file(record.operation_id)
    tmp = f.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(record.to_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, f)
    except OSError as exc:
        raise DataDirError(f"operation journal 写入失败: {f} ({exc})") from exc


def load_operation(operation_id: str) -> OperationRecord | None:
    try:
        data = json.loads(journal_file(operation_id).read_text(encoding="utf-8"))
        return OperationRecord(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
