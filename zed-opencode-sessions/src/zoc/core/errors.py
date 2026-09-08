"""自定义异常类型。"""

from __future__ import annotations


class ZocError(Exception):
    """所有 zoc 异常的基类"""


class NotFoundError(ZocError, LookupError):
    """资源未找到异常（session/thread 等）"""


class SchemaError(ZocError):
    """数据库 schema 不匹配异常"""
