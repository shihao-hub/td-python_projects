from datetime import date
from pathlib import Path

import flet as ft
import structlog

import core
import log
import storage

logger = structlog.get_logger(__name__)

_FUTURE_COLOR = ft.Colors.BLUE_400
_TODAY_COLOR = ft.Colors.ORANGE_400
_PAST_COLOR = ft.Colors.GREY_500


async def main(page: ft.Page):
    log.setup_logging()
    page.title = "数日子"
    page.padding = 12
    page.theme_mode = ft.ThemeMode.SYSTEM

    try:
        base = Path(await page.storage_paths.get_application_support_directory())
    except Exception:
        base = Path(__file__).parent / "data"
    logger.info("",base=base)
    db_file = base / "day_entries.db"
    storage.init(db_file)
    logger.info("app_started", db=str(db_file))

    items_view = ft.ListView(expand=True, spacing=6)
    empty_hint = ft.Text(
        "还没有记录，点右上角 + 添加",
        color=_PAST_COLOR,
        size=16,
        visible=False,
    )

    name_field = ft.TextField(label="名称")
    year_dd = ft.Dropdown(
        label="年",
        expand=3,
        options=[ft.dropdown.Option(str(y)) for y in range(1900, date.today().year + 101)],
    )
    month_dd = ft.Dropdown(
        label="月",
        expand=2,
        options=[ft.dropdown.Option(f"{m:02d}") for m in range(1, 13)],
    )
    day_dd = ft.Dropdown(
        label="日",
        expand=2,
        options=[ft.dropdown.Option(f"{d:02d}") for d in range(1, 32)],
    )
    form_error = ft.Text("", color=ft.Colors.RED_400, size=13)

    def snackbar(msg: str):
        page.show_dialog(ft.SnackBar(ft.Text(msg)))

    def badge_color(days: int):
        if days > 0:
            return _FUTURE_COLOR
        if days == 0:
            return _TODAY_COLOR
        return _PAST_COLOR

    def make_row(item: dict) -> ft.ListTile:
        days = core.days_until(item["date"], date.today())
        return ft.ListTile(
            title=ft.Text(item["name"], size=18, weight=ft.FontWeight.W_600),
            subtitle=ft.Text(item["date"]),
            trailing=ft.Row(
                [
                    ft.Text(
                        core.label(days),
                        size=14,
                        color=badge_color(days),
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.IconButton(
                        ft.Icons.EDIT_OUTLINED,
                        tooltip="编辑",
                        on_click=lambda e, it=item: open_form(it),
                    ),
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        tooltip="删除",
                        on_click=lambda e, it=item: remove(it),
                    ),
                ],
                tight=True,
            ),
        )

    def refresh():
        items = core.sort_items(storage.list_all(), date.today())
        items_view.controls = [make_row(i) for i in items]
        empty_hint.visible = not items
        page.update()

    def remove(item: dict):
        storage.delete(item["id"])
        snackbar(f"已删除「{item['name']}」")
        refresh()

    def open_form(item: dict | None = None):
        editing = item
        d = date.fromisoformat(item["date"]) if item else date.today()
        name_field.value = item["name"] if item else ""
        year_dd.value = str(d.year)
        month_dd.value = f"{d.month:02d}"
        day_dd.value = f"{d.day:02d}"
        form_error.value = ""

        def save(e):
            name = name_field.value.strip()
            if not name:
                form_error.value = "名称不能为空"
                page.update()
                return
            try:
                iso = (
                    f"{int(year_dd.value):04d}-"
                    f"{int(month_dd.value):02d}-{int(day_dd.value):02d}"
                )
                date.fromisoformat(iso)
            except (TypeError, ValueError):
                form_error.value = "日期无效（如 2 月 30 日）"
                page.update()
                return
            if editing:
                storage.update(editing["id"], name, iso)
            else:
                storage.create(name, iso)
            page.pop_dialog()
            refresh()

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("编辑记录" if editing else "添加记录"),
                content=ft.Column(
                    [
                        name_field,
                        ft.Row([year_dd, month_dd, day_dd]),
                        form_error,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton("取消", on_click=lambda e: page.pop_dialog()),
                    ft.FilledButton("保存", on_click=save),
                ],
            )
        )

    page.appbar = ft.AppBar(
        title=ft.Text("数日子"),
        actions=[
            ft.IconButton(ft.Icons.ADD, tooltip="添加", on_click=lambda e: open_form())
        ],
    )
    page.add(
        ft.Column(
            [empty_hint, items_view],
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )
    refresh()


ft.run(main)
