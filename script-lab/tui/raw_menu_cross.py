import os
import sys

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty

WINDOWS_ARROWS = {"H": "up", "P": "down"}
POSIX_ARROWS = {"[A": "up", "[B": "down"}


def read_key_windows():
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return WINDOWS_ARROWS.get(code, "")
    if ch == "\r":
        return "enter"
    return ch


def read_key_posix():
    ch = sys.stdin.read(1)
    if ch != "\x1b":
        return ch
    ready, _, _ = select.select([sys.stdin], [], [], 0.05)
    if not ready:
        return "q"
    seq = sys.stdin.read(2)
    return POSIX_ARROWS.get(seq, "")


if os.name == "nt":
    read_key = read_key_windows
else:
    read_key = read_key_posix


def draw(options, idx):
    sys.stdout.write("\033[H")
    sys.stdout.write("=== Select an Option (Up/Down, Enter, q quit) ===\n\r")
    for i, opt in enumerate(options):
        prefix = " > " if i == idx else "   "
        sys.stdout.write(f"{prefix}{opt}\033[K\n\r")
    sys.stdout.flush()


def main():
    options = ["FastAPI", "PostgreSQL", "Exit"]
    idx = 0
    old_termios = None
    if os.name != "nt":
        old_termios = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
    sys.stdout.write("\033[?1049h\033[?25l")
    try:
        while True:
            draw(options, idx)
            k = read_key()
            if k in ("q", "\x1b", "\x03"):
                break
            if k == "up" and idx > 0:
                idx -= 1
            elif k == "down" and idx < len(options) - 1:
                idx += 1
            elif k == "enter":
                if options[idx] == "Exit":
                    break
                sys.stdout.write(f"\r\n selected: {options[idx]}\n\r")
                sys.stdout.flush()
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
        if os.name != "nt" and old_termios is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_termios)


if __name__ == "__main__":
    main()
