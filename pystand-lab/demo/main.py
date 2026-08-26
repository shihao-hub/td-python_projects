"""PyStand 踩坑实验 demo：一个 App 验证三个点。

1. 嵌入式解释器加载：显示 sys.version / sys.executable
2. site-packages 依赖：import requests 显示版本
3. tkinter 补齐：窗口本身能弹出来
"""
import os
import sys
import tkinter as tk
from tkinter import ttk

import requests


def build_info_text():
    lines = [
        f"Python  : {sys.version}",
        f"exe     : {sys.executable}",
        f"argv    : {sys.argv}",
        f"PYSTAND : {getattr(sys, 'PYSTAND_HOME', '<none>')}",
        f"requests: {requests.__version__}",
        f"TCL_LIB : {os.environ.get('TCL_LIBRARY', '<none>')}",
    ]
    return "\n".join(lines)


def main():
    root = tk.Tk()
    root.title("PyStand Demo")
    root.geometry("640x240")

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill=tk.BOTH, expand=True)

    label = ttk.Label(frame, text=build_info_text(), font=("Consolas", 10))
    label.pack(anchor=tk.W)

    ttk.Button(frame, text="Quit", command=root.destroy).pack(anchor=tk.E, pady=(10, 0))
    root.mainloop()


if __name__ == "__main__":
    main()
