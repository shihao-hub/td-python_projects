from pathlib import Path
from typing import Protocol


class Task:
    """任务类"""

    def __init__(self, src_dir: Path, dest_dir: Path) -> None:
        self.src_dir: Path = src_dir
        self.dest_dir: Path = dest_dir


def scan(task: Task):
    """执行扫描"""
    pass


def main() -> None:
    print("Hello from file-sync-py!")
    # 需求：
    #   把源目录和目标目录对比，让目标变得和源一致
    # 过程：
    #   初始化任务
    #   从任务信息中获取 s 和 d 目录，进行扫描和对比
    #   扫描对比完成后，进行复制 or 删除等操作
    task = Task()
