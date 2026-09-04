"""Django 6.0+ 内置任务框架 django.tasks（batteries included 的新电池）。

默认 immediate 后端同步执行；换生产后端（如数据库/队列）只需改 settings.TASKS，
业务代码一行不动 —— 这就是「可插拔后端」的设计思想（同 CACHES / STORAGES）。
"""

import logging

from django.tasks import task

logger = logging.getLogger(__name__)


@task
def notify_book_returned(book_title: str, borrower_name: str) -> str:
    """归还通知：demo 里只写日志，模拟慢操作（发邮件/推送）。"""
    logger.info("[task] 《%s》已由 %s 归还，可再次借阅。", book_title, borrower_name)
    return "notified"
