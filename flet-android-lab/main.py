import flet as ft


def main(page: ft.Page):
    page.title = "Flet Android Lab"
    page.theme_mode = ft.ThemeMode.SYSTEM

    counter = ft.Text("0", size=64, weight=ft.FontWeight.BOLD)

    def decrement(e):
        counter.value = str(int(counter.value) - 1)
        page.update()

    def increment(e):
        counter.value = str(int(counter.value) + 1)
        page.update()

    page.add(
        ft.Container(
            content=counter,
            alignment=ft.Alignment.CENTER,
            expand=True,
        ),
        ft.Row(
            [
                ft.FilledButton("−", on_click=decrement, expand=True),
                ft.FilledButton("+", on_click=increment, expand=True),
            ]
        ),
    )


ft.run(main)
