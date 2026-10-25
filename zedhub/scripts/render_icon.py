# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=10"]
# ///
"""zedhub 专属图标生成：三环枢纽（原创，避开 Google 四色与 A/Z 形）。

用法：
    uv run scripts/render_icon.py

产物：
    src/zedhub/gui/assets/icon.png       1024 主图（壳窗口 / Nuitka 源图）
    src/zedhub/gui/assets/icon.ico       16..256 多尺寸（exe 图标）
    src/zedhub/gui/assets/icon-256.png   托盘图标
    src/zedhub/webui/icon.svg            PWA/WebUI 矢量图（与位图同源几何）
    src/zedhub/webui/icon-512.png        PWA 位图
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
SS = 4  # 超采样倍数
S = SIZE * SS

ROOT = Path(__file__).resolve().parents[1]
GUI_ASSETS = ROOT / "src" / "zedhub" / "gui" / "assets"
WEBUI = ROOT / "src" / "zedhub" / "webui"

BG_TOP = (24, 27, 34)
BG_BOTTOM = (9, 10, 13)
RING_COLORS = [(46, 211, 183), (168, 217, 90), (242, 160, 61)]  # 青绿 / 黄绿 / 琥珀
CORE_COLOR = (232, 253, 248)

PLATE_INSET = 26
PLATE_RADIUS = 232
CENTER = SIZE / 2
RING_RADIUS = 332
RING_WIDTH = 58
ARC_SPAN = 84
ARC_STARTS = (-90.0, 30.0, 150.0)
SPOKE_INNER = 152
SPOKE_OUTER = 292
SPOKE_WIDTH = 42
CORE_RADIUS = 86


def _scaled(value: float) -> float:
    return value * SS


def _polar(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    rad = math.radians(degrees)
    return cx + radius * math.cos(rad), cy + radius * math.sin(rad)


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def _draw_plate(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    inset = _scaled(PLATE_INSET)
    radius = _scaled(PLATE_RADIUS)
    x0, y0 = inset, inset
    x1, y1 = S - inset, S - inset
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=255)
    for y in range(S):
        t = y / (S - 1)
        draw.line([(0, y), (S, y)], fill=_lerp(BG_TOP, BG_BOTTOM, t))
    img.putalpha(mask)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (x0, y0, x1, y1), radius=radius, outline=(46, 52, 64), width=_scaled(5)
    )


def _draw_ring(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    half = _scaled(RING_WIDTH) / 2
    for color, start in zip(RING_COLORS, ARC_STARTS):
        box = (
            _scaled(CENTER - RING_RADIUS),
            _scaled(CENTER - RING_RADIUS),
            _scaled(CENTER + RING_RADIUS),
            _scaled(CENTER + RING_RADIUS),
        )
        draw.arc(box, start=start, end=start + ARC_SPAN, fill=color, width=_scaled(RING_WIDTH))
        for angle in (start, start + ARC_SPAN):
            x, y = _polar(_scaled(CENTER), _scaled(CENTER), _scaled(RING_RADIUS), angle)
            draw.ellipse((x - half, y - half, x + half, y + half), fill=color)


def _draw_spokes(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    half = _scaled(SPOKE_WIDTH) / 2
    for color, angle in zip(RING_COLORS, ARC_STARTS):
        x0, y0 = _polar(_scaled(CENTER), _scaled(CENTER), _scaled(SPOKE_INNER), angle)
        x1, y1 = _polar(_scaled(CENTER), _scaled(CENTER), _scaled(SPOKE_OUTER), angle)
        draw.line((x0, y0, x1, y1), fill=color, width=_scaled(SPOKE_WIDTH))
        for x, y in ((x0, y0), (x1, y1)):
            draw.ellipse((x - half, y - half, x + half, y + half), fill=color)


def _draw_core(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    r = _scaled(CORE_RADIUS)
    cx = cy = _scaled(CENTER)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=CORE_COLOR)


def render_png() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _draw_plate(img)
    _draw_ring(img)
    _draw_spokes(img)
    _draw_core(img)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def _svg_arc(cx: float, cy: float, radius: float, start: float, span: float) -> str:
    x0, y0 = _polar(cx, cy, radius, start)
    x1, y1 = _polar(cx, cy, radius, start + span)
    return (
        f"M {x0:.2f} {y0:.2f} A {radius:.2f} {radius:.2f} 0 0 1 {x1:.2f} {y1:.2f}"
    )


def render_svg() -> str:
    c = CENTER
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="100%" height="100%">',
        "  <defs>",
        '    <linearGradient id="plate" x1="0%" y1="0%" x2="0%" y2="100%">',
        f'      <stop offset="0%" stop-color="#{BG_TOP[0]:02x}{BG_TOP[1]:02x}{BG_TOP[2]:02x}" />',
        f'      <stop offset="100%" stop-color="#{BG_BOTTOM[0]:02x}{BG_BOTTOM[1]:02x}{BG_BOTTOM[2]:02x}" />',
        "    </linearGradient>",
        "  </defs>",
        f'  <rect x="{PLATE_INSET}" y="{PLATE_INSET}" width="{SIZE - PLATE_INSET * 2}" '
        f'height="{SIZE - PLATE_INSET * 2}" rx="{PLATE_RADIUS}" fill="url(#plate)" '
        'stroke="#2e3440" stroke-width="5" />',
    ]
    for color, start in zip(RING_COLORS, ARC_STARTS):
        hex_color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
        lines.append(
            f'  <path d="{_svg_arc(c, c, RING_RADIUS, start, ARC_SPAN)}" fill="none" '
            f'stroke="{hex_color}" stroke-width="{RING_WIDTH}" stroke-linecap="round" />'
        )
        x0, y0 = _polar(c, c, SPOKE_INNER, start)
        x1, y1 = _polar(c, c, SPOKE_OUTER, start)
        lines.append(
            f'  <line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
            f'stroke="{hex_color}" stroke-width="{SPOKE_WIDTH}" stroke-linecap="round" />'
        )
    lines.append(
        f'  <circle cx="{c}" cy="{c}" r="{CORE_RADIUS}" fill="#{CORE_COLOR[0]:02x}'
        f'{CORE_COLOR[1]:02x}{CORE_COLOR[2]:02x}" />'
    )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    GUI_ASSETS.mkdir(parents=True, exist_ok=True)
    WEBUI.mkdir(parents=True, exist_ok=True)

    image = render_png()
    image.save(GUI_ASSETS / "icon.png")
    image.resize((256, 256), Image.LANCZOS).save(GUI_ASSETS / "icon-256.png")
    image.save(
        GUI_ASSETS / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    image.resize((512, 512), Image.LANCZOS).save(WEBUI / "icon-512.png")
    (WEBUI / "icon.svg").write_text(render_svg(), encoding="utf-8", newline="\n")

    for path in (
        GUI_ASSETS / "icon.png",
        GUI_ASSETS / "icon-256.png",
        GUI_ASSETS / "icon.ico",
        WEBUI / "icon-512.png",
        WEBUI / "icon.svg",
    ):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
