"""zedhub 双通道构建：Nuitka 产 zedhub-gui / zedhub-cli（go-wails 规范的 Python 版）。

用法（项目目录内）：

    uv run scripts/build_exe.py            # 自动判定通道（使用项目 venv 的 dev 依赖）
    uv run scripts/build_exe.py --dir      # standalone 目录版
    uv run scripts/build_exe.py --release  # 仅在干净 tag 上允许

判定规则：
    HEAD 精确命中 zedhub-v* tag 且工作区干净（剔除 bin/dist 生成物） → release
    其余情况 → dev（文件名带 -dev 后缀）

产物（统一落 bin/）：
    dev:     bin/zedhub-gui-dev.exe  + bin/zedhub-cli-dev.exe
    release: bin/zedhub-gui.exe      + bin/zedhub-cli.exe

要点：
    - 两个产物子系统不同：GUI 用 --windows-console-mode=disable，CLI 用 force；
      禁止把两者合成单产物（GUI 子系统拿不到终端管道，会顶掉 prompt）。
    - 图标与 PE 版本资源由 --windows-icon-from-ico / --file-version 等写入；
    - 打包前需 MSVC（VS 2022 Build Tools）或 MinGW64，Nuitka 自动探测；
    - 数据资产（webui / gui/assets）必须显式 include，否则运行期读不到。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
ENTRY_GUI = PROJECT_DIR / "scripts" / "gui_entry.py"
ENTRY_CLI = PROJECT_DIR / "scripts" / "cli_entry.py"
ICON = PROJECT_DIR / "src" / "zedhub" / "gui" / "assets" / "icon.ico"
PYTHON_ICON = (
    PROJECT_DIR.parent.parent
    / "docs"
    / "assets"
    / "projects"
    / "python_projects"
    / "python-default.ico"
)
OUT_DIR = PROJECT_DIR / "bin"
PRODUCT_NAME = "ZedHub"
COMPANY_NAME = "ZedHub"
COPYRIGHT = "Copyright (c) 2026 zedhub"

COMMON_PACKAGES = (
    "zedhub",
    "pystray",
    "PIL",
    "pythonnet",
    "clr_loader",
    "uvicorn",
    "starlette",
    "typer",
    "pydantic",
    "mcp",
    "websockets",
)


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _worktree_clean() -> bool:
    """忽略构建产物目录后的工作区干净判定。"""
    status = _run_git("status", "--porcelain")
    if not status:
        return True
    dirty = [
        line
        for line in status.splitlines()
        if not line[3:].startswith(("bin/", "dist/", "build/"))
    ]
    return not dirty


def _release_allowed() -> tuple[bool, str]:
    version = _package_version()
    tagged = _run_git("tag", "--points-at", "HEAD")
    hit = [tag for tag in tagged.splitlines() if re.fullmatch(r"zedhub-v.+", tag)]
    if not hit:
        return False, f"HEAD 未命中 zedhub-v* tag（当前 tag: {tagged or '无'}）"
    if not _worktree_clean():
        return False, "工作区不干净（先提交或清理）"
    if ".dev" in version or "+" in version:
        return False, f"包版本不是干净发布号: {version}"
    return True, hit[0]


def _package_version() -> str:
    sys.path.insert(0, str(PROJECT_DIR / "src"))
    from zedhub.buildid import package_version

    return package_version()


def _pe_version(version: str) -> str:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return "0.0.0.0"
    return ".".join(match.groups()) + ".0"


def _icon_for(channel: str) -> tuple[Path, bool]:
    """dev 用 Python 双蛇（同时打包为运行期 icon-dev.ico）；release 用专属图标。"""
    if channel != "release" and PYTHON_ICON.is_file():
        return PYTHON_ICON, True
    if channel != "release":
        print(f"warning: 未找到 {PYTHON_ICON}，dev 构建回退专属图标", file=sys.stderr)
    return ICON, False


def _common_args(
    output_name: str,
    description: str,
    version: str,
    *,
    icon: Path,
    bundle_dev_icon: bool,
) -> list[str]:
    args = [
        "--assume-yes-for-downloads",
        f"--output-dir={OUT_DIR}",
        f"--output-filename={output_name}",
        f"--company-name={COMPANY_NAME}",
        f"--product-name={PRODUCT_NAME}",
        f"--file-description={description}",
        f"--file-version={_pe_version(version)}",
        f"--product-version={_pe_version(version)}",
        f"--copyright={COPYRIGHT}",
        f"--windows-icon-from-ico={icon}",
        f"--include-data-dir={PROJECT_DIR / 'src' / 'zedhub' / 'webui'}=zedhub/webui",
        f"--include-data-dir={PROJECT_DIR / 'src' / 'zedhub' / 'gui' / 'assets'}=zedhub/gui/assets",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=ruff",
    ]
    if bundle_dev_icon:
        args.append(
            f"--include-data-file={PYTHON_ICON}=zedhub/gui/assets/icon-dev.ico"
        )
    for package in COMMON_PACKAGES:
        args.append(f"--include-package={package}")
    return args


def _build(
    entry: Path,
    output_name: str,
    description: str,
    version: str,
    *,
    gui: bool,
    onefile: bool,
    channel: str,
) -> None:
    icon, bundle_dev_icon = _icon_for(channel)
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        str(entry),
        *_common_args(
            output_name,
            description,
            version,
            icon=icon,
            bundle_dev_icon=bundle_dev_icon,
        ),
    ]
    cmd.append("--windows-console-mode=" + ("disable" if gui else "force"))
    if onefile:
        cmd.append("--onefile")
        base_name = Path(output_name).stem
        cmd.append(
            f"--onefile-tempdir-spec={{CACHE_DIR}}/{COMPANY_NAME}/onefile/{base_name}/{{VERSION}}"
        )
    else:
        cmd.append("--standalone")
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_DIR, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 zedhub 双通道产物")
    parser.add_argument("--release", action="store_true", help="尝试 release 构建（无干净 tag 时退出码 2）")
    parser.add_argument("--dir", action="store_true", help="standalone 目录版（默认 onefile 单文件）")
    parser.add_argument("--only", choices=("gui", "cli"), help="只构建一个通道")
    args = parser.parse_args()

    allowed, reason = _release_allowed()
    if args.release and not allowed:
        print(f"release 门禁拒绝：{reason}", file=sys.stderr)
        return 2
    channel = "release" if allowed else "dev"
    version = _package_version()
    suffix = "" if channel == "release" else "-dev"
    print(f"channel={channel} version={version} onefile={not args.dir}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.only != "cli":
        _build(
            ENTRY_GUI,
            f"zedhub-gui{suffix}.exe",
            "ZedHub Desktop Shell",
            version,
            gui=True,
            onefile=not args.dir,
            channel=channel,
        )
    if args.only != "gui":
        _build(
            ENTRY_CLI,
            f"zedhub-cli{suffix}.exe",
            "ZedHub Command Line",
            version,
            gui=False,
            onefile=not args.dir,
            channel=channel,
        )

    print(f"done -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
