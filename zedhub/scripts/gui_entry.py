"""Nuitka GUI 入口（无控制台子系统，只开窗口）。"""

from __future__ import annotations

from zedhub.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
