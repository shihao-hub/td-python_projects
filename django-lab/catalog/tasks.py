"""Django 6.0+ 内置任务框架 django.tasks（batteries included 的新电池）。

默认 immediate 后端同步执行；换生产后端（如数据库/队列）只需改 settings.TASKS，
业务代码一行不动 —— 这就是「可插拔后端」的设计思想（同 CACHES / STORAGES）。
"""

import structlog
from django.tasks import task

logger = structlog.get_logger(__name__)


@task
def notify_book_returned(book_title: str, borrower_name: str) -> str:
    """归还通知：demo 里只写日志，模拟慢操作（发邮件/推送）。

    immediate 后端在请求线程内同步执行：这条日志会自动携带中间件绑定的
    请求上下文（http_method/http_path/user）—— 结构化日志「上下文随请求
    流动」的直观演示。
    """
    logger.info("book_returned", book_title=book_title, borrower_name=borrower_name)
    return "notified"
