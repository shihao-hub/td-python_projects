import json
import os
from pathlib import Path

# 数据目录规范：会话存储统一放 %APPDATA%\language_projects\next-scene\。
# 必须在 import nicegui 之前设置（NiceGUI 的 Storage.path 在 import 时求值）。
_appdata = os.environ.get("APPDATA")
_base = Path(_appdata) / "language_projects" if _appdata else Path.home() / ".language_projects"
_storage_dir = _base / "next-scene"
_storage_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(_storage_dir))

from nicegui import app, ui  # noqa: E402

from next_scene.llm import LLMError, suggest_directions, write_scene  # noqa: E402
from next_scene.samples import SAMPLES  # noqa: E402

MAX_PREVIOUS_CHARS = 5000

_active_clients = {"count": 0}


class ClientCounterMiddleware:
    """统计当前 WebSocket 连接数（即打开的客户端页面数），运行在主服务进程，跨客户端进程共享。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return
        _active_clients["count"] += 1
        try:
            await self.app(scope, receive, send)
        finally:
            _active_clients["count"] -= 1


app.add_middleware(ClientCounterMiddleware)


def copy_to_clipboard(text):
    ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text, ensure_ascii=False)})")
    ui.notify("已复制到剪贴板", type="info")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/client-count")
async def client_count():
    return {"count": _active_clients["count"]}


@ui.page("/")
def index():
    state = app.storage.user

    def labeled(label_text, content):
        with ui.column().classes("gap-0"):
            ui.label(label_text).classes("text-xs text-gray-400")
            ui.label(content).classes("text-sm leading-relaxed")

    def save_inputs():
        state["previous"] = previous.value or ""
        state["stuck"] = stuck.value or ""
        state["constraints"] = constraints.value or ""
        update_stale_hint()

    def update_stale_hint():
        current = (
            (previous.value or "").strip(),
            (stuck.value or "").strip(),
            (constraints.value or "").strip(),
        )
        stale_badge.set_visibility(bool(state.get("directions")) and state.get("input_snapshot") != current)

    def fill_sample():
        key = sample_select.value
        if not key:
            ui.notify("请先选择一个示例", type="warning")
            return
        sample = SAMPLES[key]
        previous.value = sample["previous"]
        stuck.value = sample["stuck"]
        constraints.value = sample.get("constraints", "")
        save_inputs()
        ui.notify(f"已填入「{key}」示例，可以直接生成方向", type="info")

    def start_over():
        state.clear()
        previous.value = ""
        stuck.value = ""
        constraints.value = ""
        sample_select.value = None
        render_directions()
        render_scene_section()
        update_stale_hint()
        ui.notify("已清空全部内容，重新开始", type="info")

    async def on_generate():
        prev = (previous.value or "").strip()
        point = (stuck.value or "").strip()
        cons = (constraints.value or "").strip()
        if not prev or not point:
            ui.notify("请先填写前文和卡点", type="warning")
            return
        if len(prev) > MAX_PREVIOUS_CHARS:
            prev = prev[:MAX_PREVIOUS_CHARS]
            previous.value = prev
            ui.notify(f"前文已截断至 {MAX_PREVIOUS_CHARS} 字", type="warning")
        state["previous"], state["stuck"], state["constraints"] = prev, point, cons
        state["input_snapshot"] = (prev, point, cons)
        generate_btn.disable()
        try:
            directions = await suggest_directions(prev, point, cons)
            state["directions"] = directions
            state["selected"] = None
            render_directions()
            render_scene_section()
            ui.notify("已生成 3 个发展方向", type="positive")
        except LLMError as error:
            ui.notify(str(error), type="negative")
        finally:
            generate_btn.enable()

    def select_direction(index):
        state["selected"] = index
        render_directions()
        render_scene_section()
        scene_section_card.run_method("scrollIntoView", {"behavior": "smooth"})

    def render_directions():
        directions_box.clear()
        directions = state.get("directions") or []
        selected = state.get("selected")
        with directions_box:
            if not directions:
                ui.label("还没有方向。填好第 1 步后点击「生成发展方向」。").classes("text-gray-400")
                return
            with ui.element("div").classes("grid grid-cols-1 md:grid-cols-3 gap-4 w-full"):
                for i, direction in enumerate(directions):
                    highlight = "ring-2 ring-blue-400" if selected == i else ""
                    with ui.card().classes(f"flex flex-col gap-3 {highlight}"):
                        ui.label(f"{i + 1} · {direction['title']}").classes("text-lg font-semibold")
                        labeled("接下来", direction["action"])
                        labeled("承接前文", direction["link"])
                        labeled("后果", direction["consequence"])
                        if selected == i:
                            ui.button("当前选择", icon="check").classes("mt-auto").props("disable")
                        else:
                            ui.button("选这个方向", on_click=lambda index=i: select_direction(index)).classes("mt-auto").props("outline")

    def render_scene_section():
        scene_box.clear()
        selected = state.get("selected")
        with scene_box:
            if selected is None:
                ui.label("选择一个方向后，在这里生成下一场戏。").classes("text-gray-400")
                return
            direction = (state.get("directions") or [])[selected]
            with ui.row().classes("items-baseline gap-2"):
                ui.label("方向").classes("text-xs text-gray-400")
                ui.label(direction["title"]).classes("text-base font-semibold")
            adjustment = ui.input(
                "调整意见（选填，例如：对白再克制一点）",
                value=state.get("adjustment", ""),
                on_change=lambda event: state.__setitem__("adjustment", event.value),
            ).classes("w-full")

            async def on_write():
                write_btn.disable()
                try:
                    scene = await write_scene(
                        (previous.value or "").strip(),
                        (constraints.value or "").strip(),
                        direction,
                        (adjustment.value or "").strip(),
                    )
                    state["scene"] = scene
                    ui.notify("已生成正文，可直接编辑", type="positive")
                    render_scene_section()
                except LLMError as error:
                    ui.notify(str(error), type="negative")
                    write_btn.enable()

            write_btn = ui.button("生成下一场戏", icon="edit", on_click=on_write)
            scene = state.get("scene")
            if scene:
                ui.label("正文（可编辑）").classes("text-xs text-gray-400 mt-2")
                scene_editor = ui.textarea(
                    value=scene,
                    on_change=lambda event: state.__setitem__("scene", event.value),
                ).classes("w-full").props("autogrow")
                with ui.row().classes("gap-3"):
                    ui.button("复制正文", icon="content_copy", on_click=lambda: copy_to_clipboard(scene_editor.value)).props("outline")
                    ui.button("下载 TXT", icon="download", on_click=lambda: ui.download(scene_editor.value.encode("utf-8"), filename="next-scene.txt")).props("outline")

    with ui.column().classes("w-full max-w-4xl mx-auto px-6 py-10 gap-10"):
        with ui.row().classes("items-center gap-4 w-full"):
            ui.label("下一场").classes("text-3xl font-bold")
            ui.label("把卡住的故事，推进成下一场戏").classes("text-lg text-gray-500")
            ui.button("重新开始", icon="restart_alt", on_click=start_over).props("outline").classes("ml-auto")
            with ui.card().classes("flex flex-col items-center gap-0").style("padding: 0.25rem 0.9rem"):
                client_count_label = ui.label("–").classes("text-2xl font-bold leading-tight")
                ui.label("当前连接").classes("text-xs text-gray-400")

        ui.add_body_html(f"""
        <script>
        (function () {{
          function refresh() {{
            fetch("/client-count")
              .then(function (r) {{ return r.json(); }})
              .then(function (d) {{
                var el = document.getElementById("c{client_count_label.id}");
                if (el) el.textContent = String(d.count);
              }});
          }}
          setInterval(refresh, 2000);
          refresh();
        }})();
        </script>
        """)

        with ui.card().classes("w-full"):
            ui.label("1 · 交代你的故事").classes("text-xl font-semibold mb-2")
            previous = ui.textarea(
                "刚写完的前文（必填）",
                value=state.get("previous", ""),
                on_change=lambda event: save_inputs(),
            ).classes("w-full").props("autogrow maxlength=5000 counter")
            stuck = ui.textarea(
                "你卡在哪里（必填，一句话说清写不下去的原因）",
                value=state.get("stuck", ""),
                on_change=lambda event: save_inputs(),
            ).classes("w-full").props("autogrow")
            constraints = ui.textarea(
                "必须保留的设定（选填，AI 不会违背）",
                value=state.get("constraints", ""),
                on_change=lambda event: save_inputs(),
            ).classes("w-full").props("autogrow")
            with ui.row().classes("items-center gap-3 mt-1"):
                sample_select = ui.select(list(SAMPLES.keys()), label="试试示例").classes("w-44")
                ui.button("填入示例", on_click=fill_sample).props("outline")
            generate_btn = ui.button("生成发展方向", icon="explore", on_click=on_generate).classes("mt-2")

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3 w-full"):
                ui.label("2 · 选择一个发展方向").classes("text-xl font-semibold")
                stale_badge = ui.badge("输入已修改，建议重新生成", color="warning").classes("text-xs")
            directions_box = ui.column().classes("w-full gap-4")

        scene_section_card = ui.card().classes("w-full")
        with scene_section_card:
            ui.label("3 · 写出下一场戏").classes("text-xl font-semibold")
            scene_box = ui.column().classes("w-full gap-3")

        ui.label("内容由 AI 生成，仅供参考 · 工作内容自动保存在当前浏览器会话中").classes(
            "text-xs text-gray-400 self-center"
        )

    stale_badge.set_visibility(False)
    render_directions()
    render_scene_section()
    update_stale_hint()


def main():
    ui.run(
        title="下一场 · AI 故事工作台",
        storage_secret=os.environ.get("STORAGE_SECRET", "next-scene-dev-secret"),
        port=int(os.environ.get("PORT", "8080")),
        show=os.environ.get("NEXT_SCENE_SHOW", "1") != "0",
        reload=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
