"""终端输出辅助：让实验现象可读、可核对。"""

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


def banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def step(msg: str) -> None:
    print(f"  {msg}")


def fact(label: str, value: object) -> None:
    print(f"  >> {label}: {value}")


def check(cond: bool, ok: str, ng: str) -> bool:
    print(f"  [{'OK' if cond else 'NG'}] {ok if cond else ng}")
    return cond


def conclude(msg: str) -> None:
    print(f"\n  结论: {msg}\n")


def require_service(name: str, err: str) -> None:
    """依赖不可用时显式报错退出，绝不静默降级成模拟实现。"""
    print(f"\n!! 依赖服务不可用: {name}\n   {err}\n   启动方式见 docker-compose.yml / .env.example")
    raise SystemExit(3)
