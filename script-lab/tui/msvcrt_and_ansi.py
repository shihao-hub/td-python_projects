import sys
import msvcrt
import ctypes

def enable_vt_mode():
    """启用 Windows 控制台的 ANSI 转义序列支持 (VT100 模式)"""
    STD_OUTPUT_HANDLE = -11
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

    handle = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    mode = ctypes.c_ulong()
    ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    ctypes.windll.kernel32.SetConsoleMode(
        handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
    )

def get_key():
    """利用 msvcrt 读取 Windows 控制台击键"""
    ch = msvcrt.getch()
    # 方向键和功能键在 Windows 下会产生双字节前缀 (0x00 或 0xE0)
    if ch in (b'\x00', b'\xe0'):
        ext = msvcrt.getch()
        if ext == b'H':
            return 'UP'
        elif ext == b'P':
            return 'DOWN'
    elif ch == b'\r':
        return 'ENTER'
    elif ch == b'\x1b':
        return 'ESC'
    return ch.decode('utf-8', errors='ignore')

# 1. 开启 VT 模式以支持 ANSI 转义序列
enable_vt_mode()

options = ["FastAPI", "PostgreSQL", "Exit"]
idx = 0

# 2. 进入备用屏幕缓冲区并隐藏光标
sys.stdout.write("\033[?1049h\033[?25l")
sys.stdout.flush()

try:
    while True:
        # 光标归位到左上角 (0,0)
        sys.stdout.write("\033[H")
        sys.stdout.write("=== Select an Option (Up/Down, Enter) ===\n")
        
        for i, opt in enumerate(options):
            if i == idx:
                # 高亮选中项（绿色加粗前缀）
                sys.stdout.write(f"\033[1;32m > {opt}\033[0m\033[K\n")
            else:
                sys.stdout.write(f"   {opt}\033[K\n")
        sys.stdout.flush()

        # 等待按键输入
        k = get_key()
        if k == 'UP' and idx > 0:
            idx -= 1
        elif k == 'DOWN' and idx < len(options) - 1:
            idx += 1
        elif k == 'ENTER' or (k == 'ESC') or (k == 'ENTER' and options[idx] == "Exit"):
            if options[idx] == "Exit" or k == 'ESC':
                break
            # 选中某项后执行的逻辑
            break
finally:
    # 3. 恢复光标显示并退出备用屏幕
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()

print(f"You selected: {options[idx]}")