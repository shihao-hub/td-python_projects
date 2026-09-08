"""Pydantic 数据模型定义。"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


# ========== Zed 侧（sidebar_threads 表） ==========

class Thread(BaseModel):
    """Zed 会话元数据（从 sidebar_threads 表读取）"""
    
    id: str  # thread_id (uuid 字符串)
    session_id: str | None  # 关联 OpenCode 的 session_id
    agent_id: str  # "opencode" / "codex-acp" 等
    title: str
    archived: bool
    projects: list[str]  # folder_paths 解析后的路径列表
    created_at: datetime | None
    updated_at: datetime | None
    interacted_at: datetime | None


# ========== OpenCode 侧（session/message/part 表） ==========

class SessionMeta(BaseModel):
    """OpenCode session 表元数据"""
    
    id: str  # session_id (主键)
    project_id: str
    directory: str  # 工作目录
    title: str
    version: str  # opencode 版本
    agent: str | None  # "build" / "explore" 等
    model: dict | None  # {"id": "glm-5.3", "providerID": "...", ...}
    time_created: int  # 毫秒时间戳
    time_archived: int | None


class MessagePart(BaseModel):
    """Part 表的一条记录（实际内容）"""
    
    id: str  # part_id
    message_id: str
    type: str  # "text" / "reasoning" / "tool" / "step-start" ...
    text: str  # 正文（type=text 时有意义）
    time_created: int  # 毫秒时间戳


class Message(BaseModel):
    """Message 表 + 关联的 parts"""
    
    id: str  # message_id
    role: str  # "user" / "assistant"
    agent: str | None  # message.data 里的 agent 字段
    modelID: str | None  # 实际使用的模型
    time_created: int  # 毫秒时间戳
    parts: list[MessagePart]  # 该消息的全部 part（按 time_created 排序）


class SessionContent(BaseModel):
    """完整会话内容（session + messages）"""
    
    session: SessionMeta
    messages: list[Message]  # 按 time_created 排序
