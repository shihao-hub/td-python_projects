"""显存与设备工具：把「显存去哪了」讲清楚。

所有函数内部延迟导入 torch，保证未装 torch 时也能 `main.py list`。
"""

from __future__ import annotations

from typing import Any


def human_bytes(num: float) -> str:
    """字节数转人类可读（1024 进制）。"""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


def device_info() -> dict[str, Any]:
    """返回 CUDA 设备关键信息（名称 / 算力 / 显存 / 驱动 / CUDA 运行时）。"""
    import torch

    info: dict[str, Any] = {
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "available": torch.cuda.is_available(),
    }
    if not info["available"]:
        return info
    prop = torch.cuda.get_device_properties(0)
    info.update(
        {
            "device_name": prop.name,
            "capability": f"{prop.major}.{prop.minor}",
            "total_memory": prop.total_memory,
            "multi_processor_count": prop.multi_processor_count,
        }
    )
    return info


def reset_peak() -> None:
    import torch

    torch.cuda.reset_peak_memory_stats()


def allocated() -> int:
    import torch

    return torch.cuda.memory_allocated()


def reserved() -> int:
    import torch

    return torch.cuda.memory_reserved()


def peak() -> int:
    import torch

    return torch.cuda.max_memory_allocated()


def free_total() -> tuple[int, int]:
    """返回 (free, total) 字节数（整卡视角，含其他进程占用）。"""
    import torch

    return torch.cuda.mem_get_info()
