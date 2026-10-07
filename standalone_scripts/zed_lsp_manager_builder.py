# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "nuitka>=2.5",
#     "zstandard>=0.22",
#     "commentjson>=0.9.0",
# ]
# ///
"""zed_lsp_manager_builder.py: Nuitka 固化打包 zed_lsp_manager.exe。

用法（在任何目录均可运行）：
    uv run python_projects/standalone_scripts/zed_lsp_manager_builder.py              # 默认打包 onefile 单文件并注入图标
    uv run python_projects/standalone_scripts/zed_lsp_manager_builder.py --dir        # standalone 文件夹版（构建更快）
    uv run python_projects/standalone_scripts/zed_lsp_manager_builder.py --dry-run    # 仅打印拼装的构建命令，不执行编译
    uv run python_projects/standalone_scripts/zed_lsp_manager_builder.py --no-icon    # 不带图标

构建特性与要点（遵循仓库规范）：
1. 编译环境与入口脚本保持一致：声明了 zstandard、commentjson 依赖；
2. onefile 解压目录固定为 %LOCALAPPDATA%/zed_lsp_manager/<版本号>，首次运行解压后秒开复用；
3. 默认图标定位父仓库 docs/assets/projects/python_projects/python-default.ico；
4. 产物输出于 dist/ 目录（.gitignore 默认排除，不污染仓库）。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# 路径推导
CURRENT_DIR = Path(__file__).resolve().parent
ENTRY_SCRIPT = CURRENT_DIR / "zed_lsp_manager.py"
# 推导父仓库根目录 (standalone_scripts -> python_projects -> language_projects)
REPO_ROOT = CURRENT_DIR.parent.parent
DEFAULT_ICON = REPO_ROOT / "docs" / "assets" / "projects" / "python_projects" / "python-default.ico"
EXE_NAME = "zed_lsp_manager.exe"


def read_version() -> str:
    """从入口脚本中提取 VERSION 常量作为单一事实源。"""
    if not ENTRY_SCRIPT.is_file():
        raise SystemExit(f"未找到目标入口脚本: {ENTRY_SCRIPT}")
    text = ENTRY_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit(f"未能从 {ENTRY_SCRIPT.name} 中解析 VERSION 常量")
    return match.group(1)


def build_command(
    version: str,
    output_dir: Path,
    standalone_only: bool,
    icon_path: Optional[Path],
) -> List[str]:
    """拼装规范的 Nuitka 命令行参数。"""
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--windows-console-mode=force",
        "--assume-yes-for-downloads",
        "--noinclude-unittest-mode=nofollow",
        "--include-package=commentjson",
        f"--output-dir={output_dir}",
        f"--output-filename={EXE_NAME}",
        f"--product-name=zed_lsp_manager",
        f"--product-version={version}",
        f"--file-version={version}",
        f"--file-description=Zed Project LSP Manager",
    ]

    if not standalone_only:
        # 启用 onefile 并设定固化解压缓存目录（实现秒级复用启动）
        cmd.append("--onefile")
        cmd.append(f"--onefile-tempdir-spec={{CACHE_DIR}}/zed_lsp_manager/{version}")

    # 图标注入
    if icon_path and icon_path.is_file():
        cmd.append(f"--windows-icon-from-ico={icon_path}")
    else:
        print(f"⚠️ 图标文件不存在，跳过图标注入: {icon_path}")

    cmd.append(str(ENTRY_SCRIPT))
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nuitka 固化打包 zed_lsp_manager.exe",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--dir", action="store_true", help="standalone 文件夹版（默认 onefile 单文件）")
    parser.add_argument("--icon", default=str(DEFAULT_ICON), help="指定 .ico 图标路径")
    parser.add_argument("--no-icon", action="store_true", help="不带图标")
    parser.add_argument("--output-dir", default=str(CURRENT_DIR / "dist"), help="产物输出目录（默认 dist）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印生成的编译命令，不实际调用编译")

    args = parser.parse_args()

    version = read_version()
    print(f">> 识别到目标脚本版本: v{version}")

    icon_target: Optional[Path] = None
    if not args.no_icon:
        icon_target = Path(args.icon).resolve()

    out_dir = Path(args.output_dir).resolve()
    cmd = build_command(
        version=version,
        output_dir=out_dir,
        standalone_only=args.dir,
        icon_path=icon_target,
    )

    print("\n" + "=" * 64)
    print("Nuitka 构建命令配置:")
    print("=" * 64)
    print(" ".join(cmd))
    print("=" * 64 + "\n")

    if args.dry_run:
        print(">> [dry-run 模式] 命令组装验证通过，未启动编译。")
        return 0

    print(f">> 开始执行 Nuitka 编译打包（输出到: {out_dir}）...")
    out_dir.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print(f"\n>> 🎉 构建成功！可执行文件位于: {out_dir / EXE_NAME}")
    else:
        print(f"\n>> ❌ 构建失败，退出码: {res.returncode}")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
