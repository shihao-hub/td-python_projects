"""llm-finetune-lab 实验目录入口。

用法（在 llm-finetune-lab/ 目录下执行）：

    uv run python main.py                       # 列出全部实验
    uv run python main.py doctor                # 环境自检（CUDA / bitsandbytes / Ollama / 数据目录）
    uv run python main.py download <别名|repo>  # 下载基座模型到数据目录
    uv run python main.py run <实验> [--scenario 名称] [-- 透传参数...]

每个实验也可独立运行：uv run python -m apps.<实验> --scenario all
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Windows 终端默认 GBK，统一 UTF-8 避免中文乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# 实验注册表：顺序 = 推荐学习顺序
APPS: list[dict] = [
    {
        "name": "env_baseline",
        "goal": "环境冒烟与显存预算：CUDA/算力验证 / 理论估算表 / 4bit 加载实测 / 算力基准",
        "needs": ["gpu", "ollama(可选)"],
        "status": "done",
    },
    {
        "name": "data_factory",
        "goal": "数据工厂：Word 制度文档提取 / 分块 / QA 合成（本地 Ollama）/ 拒答样本 / 切分",
        "needs": ["gpu", "ollama"],
        "status": "done",
    },
    {
        "name": "lora_sft",
        "goal": "LoRA 微调：手写 peft+trl 全闭环（Qwen3-0.6B → 1.7B），观察 loss 与过拟合",
        "needs": ["gpu"],
        "status": "done",
    },
    {
        "name": "qlora_4b",
        "goal": "QLoRA 微调：4bit 量化 + 4B 模型，8GB 显存下的取舍与调优",
        "needs": ["gpu"],
        "status": "done",
    },
    {
        "name": "eval_lab",
        "goal": "评测：QA 正确率 / 越界拒答正确率 / 幻觉率 / 引用准确率（本地 Ollama 当裁判）",
        "needs": ["gpu", "ollama"],
        "status": "done",
    },
    {
        "name": "export_ollama",
        "goal": "导出部署：merge adapter → GGUF → Ollama 导入 → ollama run 对话验收",
        "needs": ["gpu", "ollama"],
        "status": "done",
    },
    {
        "name": "rag_compare",
        "goal": "四方对照：基座+提示词 / 轻量 RAG / 微调 / 微调+RAG，给出最终方案建议",
        "needs": ["gpu", "ollama"],
        "status": "done",
    },
]

STATUS_LABEL = {"done": "已实现", "todo": "待实现"}


def cmd_list() -> None:
    print("llm-finetune-lab 实验目录（推荐按此顺序学习）\n")
    for i, app in enumerate(APPS, 1):
        needs = "、".join(app["needs"])
        print(f"  {i:2d}. {app['name']}  [{STATUS_LABEL.get(app['status'], app['status'])}]")
        print(f"      学习目标: {app['goal']}")
        print(f"      依赖:     {needs}")
        print(f"      运行:     uv run python -m apps.{app['name']} --list")
    print("\n环境自检: uv run python main.py doctor")
    print("教程文档: 父仓库 docs/python_projects/llm-finetune-lab/")


SERVICE_HINTS = {
    "ollama": "启动 Ollama 桌面端，或命令行执行 ollama serve；数据生成需要的模型: ollama pull qwen2.5:7b",
}


def cmd_doctor() -> int:
    from apps.common import ollama_client, paths
    from apps.common.models import MODELS

    print("== 环境自检（llm-finetune-lab）==\n")
    ok_all = True

    # 1. Python
    print(f"  Python {sys.version.split()[0]}  ({sys.executable})")

    # 2. PyTorch + CUDA
    try:
        import torch

        cuda_ok = torch.cuda.is_available()
        if cuda_ok:
            prop = torch.cuda.get_device_properties(0)
            cap = f"{prop.major}.{prop.minor}"
            sm_note = "（Blackwell sm_120，cu128 构建）" if cap == "12.0" else f"（sm_{prop.major}{prop.minor}）"
            print(
                f"[OK ] PyTorch {torch.__version__} + CUDA {torch.version.cuda} | "
                f"{prop.name} {prop.total_memory / 1024**3:.1f}GB {sm_note}"
            )
        else:
            ok_all = False
            print(f"[NG ] PyTorch {torch.__version__} 但 CUDA 不可用（检查驱动 / cu128 安装）")
    except Exception as exc:  # noqa: BLE001
        ok_all = False
        print(f"[NG ] PyTorch 导入失败: {exc!r}")
        print("      提示: uv sync 应使用 pyproject.toml 中配置的 pytorch-cu128 索引")

    # 3. bitsandbytes（QLoRA 4bit 依赖）
    try:
        import bitsandbytes as bnb

        print(f"[OK ] bitsandbytes {bnb.__version__}（4bit 量化可用性在实验 01 实测）")
    except Exception as exc:  # noqa: BLE001
        ok_all = False
        print(f"[NG ] bitsandbytes 导入失败: {exc!r}")

    # 4. 训练栈版本
    import importlib.metadata as md

    for name in ("transformers", "peft", "trl", "datasets", "accelerate"):
        try:
            print(f"[OK ] {name} {md.version(name)}")
        except Exception as exc:  # noqa: BLE001
            ok_all = False
            print(f"[NG ] {name} 未安装: {exc!r}")

    # 5. Ollama（数据工厂 / 评测裁判 / RAG 对照）
    try:
        models = ollama_client.list_models()
        names = sorted(m["name"] for m in models)
        print(f"[OK ] Ollama 可达，已拉取 {len(names)} 个模型")
        for required in ("qwen2.5:7b",):
            hit = any(n == required or n.startswith(required + ":") for n in names)
            if hit:
                print(f"      {required} 已就绪（数据工厂/裁判将使用）")
            else:
                print(f"      [!!] 缺少 {required}，需要时执行: ollama pull {required}")
    except Exception as exc:  # noqa: BLE001
        ok_all = False
        print(f"[NG ] Ollama 不可达: {exc}")
        print(f"      提示: {SERVICE_HINTS['ollama']}")

    # 6. 数据目录（强约束：所有运行时数据在此）
    root = paths.bootstrap()
    print(f"[OK ] 数据目录 {root}")
    print(f"      {paths.free_space_text()}")
    print(f"      HF_HOME={paths.HF_CACHE_DIR}")
    print(f"      模型别名: {'、'.join(MODELS)}")

    print("\n结论:", "环境就绪，可以开始实验 01" if ok_all else "存在 NG 项，先修复再开始实验")
    return 0 if ok_all else 1


def _download_hf(repo: str, alias: str) -> bool:
    from apps.common import paths

    from huggingface_hub import snapshot_download

    print(f"\n[..] HF 下载 {repo} → {paths.HF_CACHE_DIR}")
    try:
        local = snapshot_download(repo_id=repo)
        print(f"[OK ] {alias}: {local}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[NG ] {alias} HF 下载失败: {exc!r}")
        print("     可设置 HF_ENDPOINT=https://hf-mirror.com 重试，或改用 --source modelscope")
        return False


def _download_modelscope(repo: str, alias: str) -> bool:
    """走 ModelScope（国内速度快、支持断点续传），落到 <数据根>/models/<名字>/。"""
    from apps.common import models

    dest = models.local_path(repo)
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv", "run", "--with", "modelscope", "modelscope", "download",
        "--model", repo,
        "--local_dir", str(dest),
    ]
    print(f"\n[..] ModelScope 下载 {repo} → {dest}")
    rc = subprocess.call(cmd)
    if rc == 0 and (dest / "config.json").is_file():
        print(f"[OK ] {alias}: {dest}")
        return True
    print(f"[NG ] {alias} ModelScope 下载失败（退出码 {rc}）")
    return False


def cmd_download(target: str, source: str) -> int:
    from apps.common import models, paths

    paths.bootstrap()

    if target == "all":
        targets = list(models.MODELS)
    else:
        targets = [target]

    failures = 0
    for t in targets:
        try:
            alias, repo = models.resolve(t)
        except KeyError as exc:
            print(f"[NG ] {exc}")
            failures += 1
            continue

        cached = models.local_path_if_exists(repo)
        if cached:
            print(f"[OK ] {alias} 已存在本地目录，跳过: {cached}")
            continue

        if source == "modelscope":
            ok = _download_modelscope(repo, alias)
        else:
            ok = _download_hf(repo, alias)
        failures += 0 if ok else 1

    if failures:
        print(f"\n结束：{failures} 个失败")
        return 1
    print("\n全部下载完成。实验可用 uv run python main.py run env_baseline --scenario measure")
    return 0


def cmd_run(app_name: str, scenario: str, extra: list[str]) -> int:
    known = {app["name"] for app in APPS}
    if app_name not in known:
        print(f"未知实验: {app_name}（uv run python main.py 查看全部）")
        return 2
    entry = next(a for a in APPS if a["name"] == app_name)
    if entry["status"] != "done":
        print(f"实验 {app_name} 尚未实现（计划中）。")
        return 2
    cmd = [sys.executable, "-m", f"apps.{app_name}"]
    if scenario:
        cmd += ["--scenario", scenario]
    cmd += extra
    return subprocess.call(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(prog="llm-finetune-lab", description="实验目录入口")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出全部实验")
    sub.add_parser("doctor", help="环境自检")
    p_dl = sub.add_parser("download", help="下载基座模型")
    p_dl.add_argument("target", help="模型别名（qwen3-0.6b / qwen3-1.7b / qwen3-4b）或 all，也可直接传 HF repo id")
    p_dl.add_argument(
        "--source",
        choices=["hf", "modelscope"],
        default="hf",
        help="下载源：hf（默认，可配 HF_ENDPOINT 镜像）/ modelscope（国内推荐，落本地 models/ 目录）",
    )
    p_run = sub.add_parser("run", help="运行某个实验")
    p_run.add_argument("app", help="实验名，如 env_baseline")
    p_run.add_argument("--scenario", default="", help="场景名或 all")
    p_run.add_argument("extra", nargs=argparse.REMAINDER, help="透传给实验的额外参数（放在 -- 之后）")

    args = parser.parse_args()
    if args.command in (None, "list"):
        cmd_list()
    elif args.command == "doctor":
        raise SystemExit(cmd_doctor())
    elif args.command == "download":
        raise SystemExit(cmd_download(args.target, args.source))
    elif args.command == "run":
        extra = args.extra
        if extra and extra[0] == "--":
            extra = extra[1:]
        raise SystemExit(cmd_run(args.app, args.scenario, extra))


if __name__ == "__main__":
    main()
