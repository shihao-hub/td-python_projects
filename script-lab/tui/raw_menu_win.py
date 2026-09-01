import sys
import msvcrt

ARROW = {"H": "up", "P": "down"}


def read_key():
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return ARROW.get(code, "")
    if ch == "\r":
        return "enter"
    return ch


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


if __name__ == "__main__":
    main()
