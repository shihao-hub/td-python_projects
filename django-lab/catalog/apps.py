from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
    verbose_name = "图书目录"

    def ready(self) -> None:
        # AppConfig.ready()：应用加载完成后的钩子，注册信号
        from . import signals  # noqa: F401
