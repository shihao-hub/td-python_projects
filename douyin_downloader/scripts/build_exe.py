# /// script
# requires-python = ">=3.12"
# dependencies = ["nuitka>=2.5", "websocket-client>=1.8", "zstandard>=0.22"]
# ///
"""构建 douyin_dl.exe（Nuitka 打包固化脚本）。

用法（项目目录内）：
    uv run scripts/build_exe.py                 # onefile 单文件，默认带图标
    uv run scripts/build_exe.py --dir           # standalone 文件夹版（启动更快）
    uv run scripts/build_exe.py --icon X.ico    # 指定图标
    uv run scripts/build_exe.py --no-icon       # 不带图标

    要点：
    - 本脚本环境必须同时安装 nuitka 与 websocket-client：
      Nuitka 靠「编译期解释器可 import」来定位第三方包，缺 websocket-client
      会导致包不被打进 exe，运行时报 ModuleNotFoundError。
      zstandard 用于 onefile 压缩（缺了只是体积更大，不影响功能）。
    - onefile 解压目录固定为 %LOCALAPPDATA%/douyin_dl/<版本>，
      首次运行解压后缓存复用（秒开），版本升级自动换新目录。
    - 依赖 MSVC（VS 2022 Build Tools），首次编译需数分钟。
    - 产物输出 dist/，子仓 .gitignore 已排除 dist/ 与 *.exe，不会入库。
    - 默认图标：父仓库 docs/assets/projects/python_projects/python-default.ico
      （见《python exe 默认图标》文档）；找不到时跳过图标并告警，不阻断构建。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENTRY = PROJECT_DIR / "douyin_dl.py"
REPO_ROOT = PROJECT_DIR.parent.parent
DEFAULT_ICON = REPO_ROOT / "docs" / "assets" / "projects" / "python_projects" / "python-default.ico"
EXE_NAME = "douyin_dl.exe"


def read_version() -> str:
    text = ENTRY.read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit(f"未能从 {ENTRY.name} 解析 VERSION")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nuitka 打包 douyin_dl.exe")
    parser.add_argument("--dir", action="store_true", help="standalone 文件夹版（默认 onefile 单文件）")
    parser.add_argument("--icon", default=str(DEFAULT_ICON), help="图标 .ico 路径")
    parser.add_argument("--no-icon", action="store_true", help="不带图标")
    parser.add_argument("--output-dir", default="dist", help="输出目录（默认 dist）")
    args = parser.parse_args()

    if not ENTRY.is_file():
        raise SystemExit(f"入口不存在: {ENTRY}")

    version = read_version()
    out_dir = (PROJECT_DIR / args.output_dir).resolve()
    cmd = [
        sys.executable, "-m", "nuitka",
        "--assume-yes-for-downloads",
        "--standalone",
        "--windows-console-mode=force",
        "--include-package=websocket",
        "--nofollow-import-to=websocket.tests",
        "--noinclude-unittest-mode=nofollow",
        f"--output-dir={out_dir}",
        f"--output-filename={EXE_NAME}",
        "--product-name=douyin_dl",
        f"--product-version={version}",
        f"--file-version={version}",
        "--file-description=Douyin video downloader (CDP)",
    ]
    if not args.dir:
        cmd += [
            "--onefile",
            # 固定解压目录（{CACHE_DIR}=%LOCALAPPDATA% 用户缓存目录，{VERSION}=file-version）：
            # 只在首次运行/版本变化时解压，之后直接复用实现秒开；
            # 代价是程序退出后目录残留（旧版本目录需手动清理）。
            # 注意：{VAR} 风格变量名见 nuitka/options/PathSpecs.py（没有 {CACHE}）；
            # {PROGRAM} 是运行期绝对路径、只能放 spec 开头，程序名直接写死。
            "--onefile-tempdir-spec={CACHE_DIR}/douyin_dl/{VERSION}",
        ]
    if not args.no_icon:
        icon = Path(args.icon)
        if icon.is_file():
            cmd.append(f"--windows-icon-from-ico={icon}")
        else:
            print(f"[warn] 图标不存在，跳过: {icon}", file=sys.stderr)
    cmd.append(str(ENTRY))

    print("[*] Nuitka 编译开始（首次需数分钟）...", file=sys.stderr)
    completed = subprocess.run(cmd, cwd=PROJECT_DIR)
    if completed.returncode != 0:
        print(f"[!] Nuitka 编译失败（退出码 {completed.returncode}）；"
              f"请确认已安装 MSVC（VS 2022 Build Tools）", file=sys.stderr)
        return completed.returncode

    artifact = (out_dir / EXE_NAME) if not args.dir else (out_dir / f"{EXE_NAME.removesuffix('.exe')}.dist" / EXE_NAME)
    if not artifact.is_file():
        print(f"[!] 未找到产物: {artifact}", file=sys.stderr)
        return 1
    print(f"[OK] 产物: {artifact} ({artifact.stat().st_size / 1024 / 1024:.1f} MB, v{version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
