"""python -m zedhub 入口（自动拉起的 spawn 路径依赖它）。"""

from __future__ import annotations

from .cli import app

if __name__ == "__main__":
    app()
