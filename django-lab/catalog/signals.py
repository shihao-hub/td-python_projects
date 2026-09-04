"""信号：解耦的业务联动。

BorrowRecord 保存后自动同步图书状态 —— 借阅处、API、admin、shell 里
任何路径创建/归还记录，状态机都保持一致，无需每个调用方记得手动改。
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import BorrowRecord, Status


@receiver(post_save, sender=BorrowRecord, dispatch_uid="catalog.sync_book_status")
def sync_book_status(sender, instance: BorrowRecord, **kwargs) -> None:
    instance.book.status = Status.BORROWED if instance.returned_at is None else Status.AVAILABLE
    instance.book.save(update_fields=["status"])
