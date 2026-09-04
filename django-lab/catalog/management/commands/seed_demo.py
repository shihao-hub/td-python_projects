"""seed_demo：Django「管理命令」电池 —— 业务脚本与 manage.py 无缝集成，可重复执行。"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Author, Book, BookGenre, BorrowRecord, Genre, Status

User = get_user_model()

GENRES = ["编程", "架构", "数据库", "Web", "算法", "人文"]

# (作者名, 姓), [(书名, slug, 分类, 定价, 摘要)]
SEED = [
    (
        ("Adrian", "Holovaty"),
        [
            (
                "Django 之道",
                "the-way-of-django",
                ["编程", "Web"],
                "59.00",
                "两位 Django 联合创造者带你回到框架设计的原点：松耦合、快速开发、不重复自己。",
            ),
        ],
    ),
    (
        ("Jacob", "Kaplan-Moss"),
        [
            (
                "模型即蓝图",
                "model-as-blueprint",
                ["编程", "架构"],
                "69.00",
                "从 models.py 出发理解 Django：一份模型定义同时驱动 schema、表单与后台。",
            ),
        ],
    ),
    (
        ("Simon", "Willison"),
        [
            (
                "管理命令的艺术",
                "art-of-management-commands",
                ["编程", "数据库"],
                "49.00",
                "把运维脚本写进代码仓库：manage.py 就是你的一站式工具箱。",
            ),
        ],
    ),
    (
        ("Kathy", "Sierra"),
        [
            (
                "深入浅出程序设计",
                "head-first-programming",
                ["编程", "人文"],
                "89.00",
                "让大脑真正吸收知识，而不是假装读过。",
            ),
        ],
    ),
    (
        ("Martin", "Kleppmann"),
        [
            (
                "数据密集型应用系统设计",
                "ddia",
                ["架构", "数据库"],
                "128.00",
                "数据系统三大主题：可靠性、可扩展性、可维护性。",
            ),
            (
                "生成列与一致性",
                "generated-columns",
                ["数据库"],
                "39.00",
                "把派生数据下推到数据库：生成列、物化视图与触发器的取舍。",
            ),
        ],
    ),
    (
        ("Andrew", "Tanenbaum"),
        [
            (
                "现代操作系统",
                "modern-operating-systems",
                ["架构"],
                "99.00",
                "进程、线程与调度：并发世界的基础设施。",
            ),
        ],
    ),
    (
        ("Luciano", "Ramalho"),
        [
            (
                "流畅的 Python",
                "fluent-python",
                ["编程", "算法"],
                "139.00",
                "写 Pythonic 的代码：协议、迭代器与描述符。",
            ),
            (
                "异步实战",
                "async-in-practice",
                ["编程", "Web"],
                "79.00",
                "从 async/await 到 ASGI：Web 框架里的协程。",
            ),
        ],
    ),
    (
        ("Ayn", "Rand"),
        [
            (
                "源泉",
                "the-fountainhead",
                ["人文"],
                "98.00",
                "个体创造力的宣言 —— 与框架设计无关，但很好看。",
            ),
        ],
    ),
]


class Command(BaseCommand):
    help = "灌入演示数据：分类 / 作者 / 图书 / 借阅记录 + 演示账号（幂等，可重复执行）"

    @transaction.atomic
    def handle(self, *args, **options):
        genres = {name: Genre.objects.get_or_create(name=name)[0] for name in GENRES}

        for (first, last), books in SEED:
            author, _ = Author.objects.get_or_create(first_name=first, last_name=last)
            for title, slug, genre_names, price, summary in books:
                book, created = Book.objects.get_or_create(
                    slug=slug,
                    defaults={"title": title, "author": author, "price": price, "summary": summary},
                )
                if created:
                    for genre_name in genre_names:
                        BookGenre.objects.get_or_create(book=book, genre=genres[genre_name])

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True, "email": "admin@lab"},
        )
        if created:
            admin.set_password("admin1234")
            admin.save()
            self.stdout.write(self.style.SUCCESS("创建管理员 admin / admin1234"))

        reader, created = User.objects.get_or_create(
            username="reader", defaults={"email": "reader@lab"}
        )
        if created:
            reader.set_password("reader1234")
            reader.save()
            self.stdout.write(self.style.SUCCESS("创建读者账号 reader / reader1234"))

        ddia: Book | None = Book.objects.filter(slug="ddia", status=Status.AVAILABLE).first()
        if ddia and not BorrowRecord.objects.filter(book=ddia, returned_at__isnull=True).exists():
            BorrowRecord.objects.create(
                book=ddia, borrower=reader, due_date=date.today() + timedelta(days=10)
            )

        # 造一本下架书和一本草稿书，便于观察状态机与 admin 筛选
        Book.objects.filter(slug="the-fountainhead").update(status=Status.RETIRED)
        Book.objects.filter(slug="generated-columns").update(status=Status.DRAFT)

        self.stdout.write(
            self.style.SUCCESS(
                f"完成：{Book.objects.count()} 本书 / {Author.objects.count()} 位作者"
            )
        )
