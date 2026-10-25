"""Nuitka CLI 入口（控制台子系统，只跑终端）。"""

from __future__ import annotations

from zedhub.cli import app

if __name__ == "__main__":
    app(prog_name="zedhub-cli")
