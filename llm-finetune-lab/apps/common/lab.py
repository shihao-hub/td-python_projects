"""终端输出辅助：让实验现象可读、可核对（风格与 tech_learning_room 对齐）。"""

from __future__ import annotations

import sys
import time

# Windows 终端默认 GBK，统一改 UTF-8 避免中文/符号乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


class Timer:
    """with 代码块计时器，精确到毫秒。"""

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed_ms = (time.perf_counter() - self._t0) * 1000

    @property
    def ms(self) -> float:
        return getattr(self, "elapsed_ms", 0.0)

    @property
    def s(self) -> float:
        return self.ms / 1000


def banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def step(msg: str) -> None:
    print(f"  {msg}")


def fact(label: str, value: object) -> None:
    print(f"  >> {label}: {value}")


def check(cond: bool, ok: str, ng: str) -> bool:
    print(f"  [{'OK' if cond else 'NG'}] {ok if cond else ng}")
    return cond


def warn(msg: str) -> None:
    print(f"  [!!] {msg}")


def conclude(msg: str) -> None:
    print(f"\n  结论: {msg}\n")


def require_gpu() -> None:
    """没有可用 CUDA 时显式退出，绝不静默退化为 CPU 训练。"""
    import torch

    if not torch.cuda.is_available():
        print(
            "\n!! 未检测到可用 CUDA GPU。\n"
            "   Windows 下请确认：\n"
            "   1) uv sync 使用的是 cu128 索引（pyproject.toml 已配置）；\n"
            "   2) 显卡驱动正常（nvidia-smi 可运行）。\n"
            "   本实验室不提供 CPU 训练降级。"
        )
        raise SystemExit(3)
