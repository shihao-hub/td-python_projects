"""实验注册表与 main.py 的静态约定测试（不需要 GPU）。"""

from __future__ import annotations


def test_registry_unique_and_complete() -> None:
    import main

    names = [app["name"] for app in main.APPS]
    assert len(names) == len(set(names)), "实验名不能重复"
    for app in main.APPS:
        assert app["goal"], f"{app['name']} 缺少 goal"
        assert app["status"] in ("done", "todo"), f"{app['name']} 状态非法"
        assert app["needs"], f"{app['name']} 缺少 needs"


def test_main_import_is_light() -> None:
    """入口不导入 torch/HF，保证无环境时 list 仍可用。"""
    import sys

    for mod in ("torch", "transformers", "peft", "trl"):
        sys.modules.pop(mod, None)
    import main  # noqa: F401

    for mod in ("torch", "transformers", "peft", "trl"):
        assert mod not in sys.modules, f"main 导入不应级联导入 {mod}"
