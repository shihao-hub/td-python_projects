"""无外部依赖的单元测试：实验注册表与场景命名的一致性。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from main import APPS

APPS_DIR = Path(__file__).resolve().parents[1] / "apps"


def test_registry_names_are_directories() -> None:
    """main.py 注册表里的每个实验都必须对应 apps/ 下的真实目录。"""
    for app in APPS:
        assert (APPS_DIR / app["name"]).is_dir(), f"apps/{app['name']} 不存在"
        assert (APPS_DIR / app["name"] / "__main__.py").is_file(), f"apps/{app['name']}/__main__.py 不存在"


def test_needs_fields_are_known_services() -> None:
    """needs 字段只允许 postgres / redis / kafka 三种服务标识。"""
    known = {"postgres", "redis", "kafka"}
    for app in APPS:
        assert set(app["needs"]) <= known, f"{app['name']} 含未知服务依赖: {app['needs']}"


def test_scenario_tables_are_consistent() -> None:
    """有场景注册表的 app：SCENARIOS 与 DESCRIPTIONS 的键必须一一对应。"""

    def _assigned_names(tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in ("SCENARIOS", "DESCRIPTIONS"):
                    names.add(target.id)
        return names

    checked = 0
    for app_dir in sorted(APPS_DIR.iterdir()):
        scen = app_dir / "scenarios.py"
        if not scen.is_file():
            continue
        tree = ast.parse(scen.read_text(encoding="utf-8"))
        if _assigned_names(tree) == {"SCENARIOS", "DESCRIPTIONS"}:
            module = __import__(
                f"apps.{app_dir.name}.scenarios", fromlist=["SCENARIOS", "DESCRIPTIONS"]
            )
            assert set(module.SCENARIOS) == set(module.DESCRIPTIONS), (
                f"apps/{app_dir.name}: SCENARIOS 与 DESCRIPTIONS 键不一致"
            )
            checked += 1

    assert checked >= 8, f"预期至少 8 个 app 带场景注册表，实际 {checked}"


@pytest.mark.parametrize("app_name", [a["name"] for a in APPS])
def test_all_apps_importable(app_name: str) -> None:
    """每个实验的 __main__ 都能被导入（不连接任何外部服务）。"""
    __import__(f"apps.{app_name}.__main__", fromlist=["main"])
