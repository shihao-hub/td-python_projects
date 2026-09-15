"""路径解析：数据目录强约束（不需要 GPU）。"""

from __future__ import annotations

from pathlib import Path


def test_resolve_data_root_prefers_override(monkeypatch, tmp_path: Path) -> None:
    from apps.common import paths

    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "custom"))
    resolved = paths.resolve_data_root()
    assert resolved == (tmp_path / "custom").resolve()


def test_resolve_data_root_uses_appdata(monkeypatch, tmp_path: Path) -> None:
    from apps.common import paths

    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    resolved = paths.resolve_data_root()
    assert resolved == tmp_path / "AppData" / "Roaming" / "language_projects" / "llm-finetune-lab"


def test_resolve_data_root_fallback_home(monkeypatch, tmp_path: Path) -> None:
    from apps.common import paths

    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    resolved = paths.resolve_data_root()
    assert resolved == tmp_path / ".language_projects" / "llm-finetune-lab"


def test_ensure_dirs_creates_full_chain(monkeypatch, tmp_path: Path) -> None:
    from apps.common import paths

    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "lp" / "llm-finetune-lab"))
    monkeypatch.setattr(paths, "DATA_ROOT", paths.resolve_data_root())
    monkeypatch.setattr(
        paths,
        "ALL_DIRS",
        tuple([paths.DATA_ROOT] + [paths.DATA_ROOT / name for name in ("corpus", "hf_cache", "datasets", "outputs", "logs")]),
    )
    root = paths.ensure_dirs()
    assert (root / "corpus").is_dir()
    assert (root / "hf_cache").is_dir()
    assert (root / "datasets").is_dir()
    assert (root / "outputs").is_dir()
    assert (root / "logs").is_dir()
