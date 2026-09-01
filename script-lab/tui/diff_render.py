import shutil
import sys
import time


class Screen:
    def __init__(self):
        size = shutil.get_terminal_size((80, 24))
        self.w = size.columns
        self.h = size.lines
        self.front = [[" "] * self.w for _ in range(self.h)]
        self.back = [[" "] * self.w for _ in range(self.h)]

    def clear(self):
        for row in self.back:
            for i in range(self.w):
                row[i] = " "

    def put(self, x, y, text):
        if not (0 <= y < self.h):
            return
        for i, ch in enumerate(text):
            if 0 <= x + i < self.w:
                self.back[y][x + i] = ch

    def swap(self):
        self.front, self.back = self.back, self.front

    def render_diff(self):
        out = []
        for y, (f_row, b_row) in enumerate(zip(self.front, self.back)):
            x = 0
            while x < self.w:
                if f_row[x] != b_row[x]:
                    out.append(f"\033[{y + 1};{x + 1}H{b_row[x]}")
                    f_row[x] = b_row[x]
                    x += 1
                    while x < self.w and f_row[x] != b_row[x]:
                        out.append(b_row[x])
                        f_row[x] = b_row[x]
                        x += 1
                else:
                    x += 1
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        return len(out)

    def render_full(self):
        out = ["\033[H"]
        for row in self.back:
            out.append("".join(row) + "\033[K\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        return self.w * self.h


def main():
    argv = sys.argv[1:]
    mode = "full" if "--full" in argv else "diff"
    frames = 150
    if "--frames" in argv:
        frames = int(argv[argv.index("--frames") + 1])

    screen = Screen()
    sys.stdout.write("\033[?1049h\033[?25l")
    ball_x, ball_y = 2, 2
    dx, dy = 1, 1
    cells_last = 0
    start = time.perf_counter()
    try:
        for n in range(frames):
            screen.clear()
            screen.put(0, 0, f" mode={mode} frame={n + 1}/{frames} cells_last_frame={cells_last}")
            screen.put(0, 1, " diff render writes only changed cells, full redraw writes everything")
            screen.put(0, 2, " Ctrl+C to quit")
            screen.put(ball_x, ball_y, "O")
            screen.put(0, screen.h - 1, f" elapsed={time.perf_counter() - start:.2f}s")
            if mode == "diff":
                cells_last = screen.render_diff()
            else:
                cells_last = screen.render_full()
            screen.swap()
            ball_x += dx
            if ball_x <= 1 or ball_x >= screen.w - 2:
                dx = -dx
                ball_x += 2 * dx
            ball_y += dy
            if ball_y <= 2 or ball_y >= screen.h - 3:
                dy = -dy
                ball_y += 2 * dy
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
