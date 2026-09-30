"""统一错误层级：稳定的字符串错误码 + 各入口映射表（NFR-5）。

`ErrorCode` 是公共契约：CLI `--json`、HTTP API、MCP、WebSocket、兼容 RPC
全部以此分类；HTTP 状态码、RPC 整数码、CLI 退出码只是各自的呈现映射。

入口无关约束：本模块不得调用 os.Exit / 操作标准流。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    INVALID_PARAMS = "invalid_params"            # 参数错误
    NOT_FOUND = "not_found"                      # 记录未找到
    SOURCE_NOT_SUPPORTED = "source_not_supported"  # 数据源未实现（Pi/claude/...）
    DATA_SOURCE_MISSING = "data_source_missing"  # db 文件不存在
    SCHEMA_INCOMPATIBLE = "schema_incompatible"  # 表/列缺失不可降级
    SNAPSHOT_FAILED = "snapshot_failed"          # 快照复制失败
    PROCESS_RUNNING = "process_running"          # 写前进程检查失败
    WRITE_FAILED = "write_failed"                # 事务回滚
    VERIFY_FAILED = "verify_failed"              # 写后复查不一致（结果未知）
    DATA_DIR_ERROR = "data_dir_error"            # 数据目录/备份目录不可写
    METHOD_NOT_SUPPORTED = "method_not_supported"  # 非白名单方法/未知端点
    INVALID_REQUEST = "invalid_request"          # 非 JSON/请求形状错
    HANDSHAKE_MISMATCH = "handshake_mismatch"    # buildID 握手失败
    DAEMON_UNREACHABLE = "daemon_unreachable"    # daemon 未运行/连接失败
    INTERNAL_ERROR = "internal_error"            # 未捕获异常


class ZedhubError(Exception):
    """入口无关的业务异常基类；code 为公共契约字符串。"""

    code = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if data is not None:
            self.data = data

    def to_payload(self) -> dict:
        out: dict = {"code": self.code.value, "message": self.message}
        if getattr(self, "data", None) is not None:
            out["data"] = self.data
        return out


class InvalidParamsError(ZedhubError):
    code = ErrorCode.INVALID_PARAMS


class NotFoundError(ZedhubError):
    code = ErrorCode.NOT_FOUND


class SourceNotSupportedError(ZedhubError):
    code = ErrorCode.SOURCE_NOT_SUPPORTED


class DataSourceMissingError(ZedhubError):
    code = ErrorCode.DATA_SOURCE_MISSING


class SchemaError(ZedhubError):
    code = ErrorCode.SCHEMA_INCOMPATIBLE


class SnapshotError(ZedhubError):
    code = ErrorCode.SNAPSHOT_FAILED


class ProcessRunningError(ZedhubError):
    code = ErrorCode.PROCESS_RUNNING


class WriteFailedError(ZedhubError):
    code = ErrorCode.WRITE_FAILED


class VerifyFailedError(ZedhubError):
    code = ErrorCode.VERIFY_FAILED


class DataDirError(ZedhubError):
    code = ErrorCode.DATA_DIR_ERROR


class MethodNotSupportedError(ZedhubError):
    code = ErrorCode.METHOD_NOT_SUPPORTED


class InvalidRequestError(ZedhubError):
    code = ErrorCode.INVALID_REQUEST


class HandshakeMismatchError(ZedhubError):
    code = ErrorCode.HANDSHAKE_MISMATCH


class DaemonUnreachableError(ZedhubError):
    code = ErrorCode.DAEMON_UNREACHABLE


# -- 各入口呈现映射（HTTP 状态 / JSON-RPC 整数码 / CLI 退出码） ----------------
# HTTP 状态码仅辅助定位；error.code 才是唯一稳定契约。

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_PARAMS: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.SOURCE_NOT_SUPPORTED: 400,
    ErrorCode.DATA_SOURCE_MISSING: 503,
    ErrorCode.SCHEMA_INCOMPATIBLE: 500,
    ErrorCode.SNAPSHOT_FAILED: 500,
    ErrorCode.PROCESS_RUNNING: 409,
    ErrorCode.WRITE_FAILED: 500,
    ErrorCode.VERIFY_FAILED: 500,
    ErrorCode.DATA_DIR_ERROR: 500,
    ErrorCode.METHOD_NOT_SUPPORTED: 404,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.HANDSHAKE_MISMATCH: 409,
    ErrorCode.INTERNAL_ERROR: 500,
}

# 兼容 JSON-RPC 旧整数码（rpc 壳冻结语义，维持归档基线）。
RPC_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_PARAMS: -32602,
    ErrorCode.NOT_FOUND: -32001,
    ErrorCode.SOURCE_NOT_SUPPORTED: -32001,
    ErrorCode.DATA_SOURCE_MISSING: -32000,
    ErrorCode.SCHEMA_INCOMPATIBLE: -32000,
    ErrorCode.SNAPSHOT_FAILED: -32000,
    ErrorCode.METHOD_NOT_SUPPORTED: -32601,
    ErrorCode.INVALID_REQUEST: -32600,
    ErrorCode.DAEMON_UNREACHABLE: -32000,
    ErrorCode.INTERNAL_ERROR: -32603,
}

# CLI 退出码：0 成功（含空结果）、1 运行错误、2 用法错误。
EXIT_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_PARAMS: 2,
    ErrorCode.NOT_FOUND: 1,
    ErrorCode.SOURCE_NOT_SUPPORTED: 1,
    ErrorCode.DATA_SOURCE_MISSING: 1,
    ErrorCode.SCHEMA_INCOMPATIBLE: 1,
    ErrorCode.SNAPSHOT_FAILED: 1,
    ErrorCode.PROCESS_RUNNING: 1,
    ErrorCode.WRITE_FAILED: 1,
    ErrorCode.VERIFY_FAILED: 1,
    ErrorCode.DATA_DIR_ERROR: 1,
    ErrorCode.METHOD_NOT_SUPPORTED: 2,
    ErrorCode.INVALID_REQUEST: 2,
    ErrorCode.HANDSHAKE_MISMATCH: 1,
    ErrorCode.DAEMON_UNREACHABLE: 1,
    ErrorCode.INTERNAL_ERROR: 1,
}
