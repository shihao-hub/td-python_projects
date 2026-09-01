# script-lab

通用测试脚本项目：存放各种一次性实验、验证、demo 脚本。按主题分子目录，各目录互不依赖。

## 目录

| 子目录 | 说明 |
| --- | --- |
| `tui/` | 终端 UI 实验：raw mode、按键解析、ANSI 渲染 |

## tui/

纯标准库实现，无第三方依赖，全部在 Windows Terminal / PowerShell 下可直接运行。

| 脚本 | 说明 | 运行 |
| --- | --- | --- |
| `raw_menu_win.py` | Windows 原生菜单：`msvcrt` 单键捕获 + 方向键扫描码解析 + ANSI 备用屏幕 | `python tui\raw_menu_win.py` |
| `raw_menu_cross.py` | 跨平台版：按 `os.name` 自动分派 `msvcrt`（Windows）或 `termios`（POSIX），逻辑同上 | `python tui\raw_menu_cross.py` |
| `diff_render.py` | 双缓冲差量渲染演示：内存维护前后两帧字符矩阵，只重绘差异单元格；`--full` 对比全屏重绘 | `python tui\diff_render.py`（差量）<br>`python tui\diff_render.py --full`（全屏重绘）<br>`--frames N` 控制帧数 |

按键约定：`↑` `↓` 移动，`Enter` 选择，`q` / `Esc` / `Ctrl+C` 退出。
