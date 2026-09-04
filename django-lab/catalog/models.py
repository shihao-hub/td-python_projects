"""数据模型：Django 哲学的核心 ——「模型即唯一事实」。

一张 models.py 同时定义：数据库 schema（迁移自动生成）、校验规则、
业务方法（fat models）与人类可读的元数据（verbose_name / __str__）。
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Now
from django.urls import reverse
from django.utils import timezone


# 状态枚举：Django 5.0 起也支持 models.TextChoices("Status", "DRAFT AVAILABLE ...")
# 函数式一行写法，但类式对类型检查器（django-stubs）更友好，且 label 可中文化
class Status(models.TextChoices):
    DRAFT = "DRAFT", "草稿"
    AVAILABLE = "AVAILABLE", "可借"
    BORROWED = "BORROWED", "已借出"
    RETIRED = "RETIRED", "已下架"


class BookQuerySet(models.QuerySet):
    """自定义 QuerySet：把常用查询收进模型层，视图保持瘦。"""

    def available(self) -> "BookQuerySet":
        return self.filter(status=Status.AVAILABLE)

    def with_relations(self) -> "BookQuerySet":
        return self.select_related("author").prefetch_related("genres")

    def search(self, keywords: str) -> "BookQuerySet":
        condition = (
            Q(title__icontains=keywords)
            | Q(summary__icontains=keywords)
            | Q(author__first_name__icontains=keywords)
            | Q(author__last_name__icontains=keywords)
        )
        return self.with_relations().filter(condition)


class Genre(models.Model):
    name = models.CharField("分类", max_length=50, unique=True)

    class Meta:
        verbose_name = verbose_name_plural = "分类"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Author(models.Model):
    first_name = models.CharField("名", max_length=50)
    last_name = models.CharField("姓", max_length=50)
    about = models.TextField("简介", blank=True)

    class Meta:
        verbose_name = "作者"
        verbose_name_plural = "作者"
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(fields=["first_name", "last_name"], name="unique_author_name"),
        ]

    def __str__(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()

    # DRY：模型知道自己详情页在哪，CreateView/admin 的跳转都复用它
    def get_absolute_url(self) -> str:
        return reverse("catalog:author-detail", args=[self.pk])

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Book(models.Model):
    title = models.CharField("书名", max_length=200)
    slug = models.SlugField("URL 标识", max_length=220, unique=True, allow_unicode=True)
    author = models.ForeignKey(
        Author, verbose_name="作者", on_delete=models.DB_CASCADE, related_name="books"
    )
    # 显式 through：Django 6.1 要求 DB 级 on_delete 在引用链（含 M2M 中间表）上统一
    genres = models.ManyToManyField(
        Genre,
        verbose_name="分类",
        blank=True,
        through="BookGenre",
        related_name="books",
    )
    summary = models.TextField("摘要", blank=True)
    isbn = models.CharField("ISBN", max_length=20, blank=True, default="")
    price = models.DecimalField(
        "定价",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    # Django 5.0+ GeneratedField：数据库生成列，写入时由 DB 计算，只读
    price_with_tax = models.GeneratedField(
        verbose_name="含税价",
        expression=F("price") * models.Value(Decimal("1.09")),
        output_field=models.DecimalField(max_digits=10, decimal_places=2),
        db_persist=True,
    )
    status = models.CharField(
        "状态", max_length=20, choices=Status.choices, default=Status.AVAILABLE
    )
    # Django 5.0+ db_default：默认值由数据库在 INSERT 时计算（CURRENT_TIMESTAMP）
    created_at = models.DateTimeField("入库时间", db_default=Now(), editable=False)

    objects = BookQuerySet.as_manager()

    class Meta:
        verbose_name = verbose_name_plural = "图书"
        ordering = ["-created_at", "title"]
        indexes = [models.Index(fields=["status"], name="book_status_idx")]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("catalog:book-detail", args=[self.slug])

    @property
    def is_borrowable(self) -> bool:
        return self.status == Status.AVAILABLE

    @property
    def active_borrow(self) -> "BorrowRecord | None":
        return self.borrows.filter(returned_at__isnull=True).first()


class BookGenre(models.Model):
    """M2M 中间表（显式声明以使用 DB 级级联）。"""

    book = models.ForeignKey(
        Book, verbose_name="图书", on_delete=models.DB_CASCADE, related_name="book_genres"
    )
    genre = models.ForeignKey(
        Genre, verbose_name="分类", on_delete=models.DB_CASCADE, related_name="book_genres"
    )

    class Meta:
        verbose_name = verbose_name_plural = "图书分类"
        ordering = ["genre__name"]
        constraints = [
            models.UniqueConstraint(fields=["book", "genre"], name="unique_book_genre"),
        ]

    def __str__(self) -> str:
        return f"{self.book} · {self.genre}"


class BorrowRecord(models.Model):
    # Django 6.1+ DB_CASCADE：删除下放给数据库的 ON DELETE 子句，不逐个加载对象。
    # 注意：同一模型的关联字段必须统一变体；DB 级联不触发 pre/post_delete 信号。
    book = models.ForeignKey(
        Book,
        verbose_name="图书",
        on_delete=models.DB_CASCADE,
        related_name="borrows",
    )
    borrower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="借阅人",
        on_delete=models.DB_CASCADE,
        related_name="borrow_records",
    )
    borrowed_at = models.DateTimeField("借出时间", db_default=Now(), editable=False)
    due_date = models.DateField("应还日期")
    returned_at = models.DateTimeField("归还时间", null=True, blank=True)

    class Meta:
        verbose_name = "借阅记录"
        ordering = ["-borrowed_at"]
        constraints = [
            # 部分唯一约束（条件索引）：同一本书同时只能有一条「未归还」记录，
            # 并发下的最终防线由数据库兜底，而非只靠应用层校验
            models.UniqueConstraint(
                fields=["book"],
                condition=Q(returned_at__isnull=True),
                name="unique_active_borrow_per_book",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.borrower} 借《{self.book}》"

    @property
    def is_active(self) -> bool:
        return self.returned_at is None

    @property
    def is_overdue(self) -> bool:
        return self.is_active and self.due_date < timezone.localdate()

    def mark_returned(self) -> None:
        """fat models：归还业务逻辑长在模型上，视图/接口/后台都能复用。"""
        if self.returned_at is None:
            self.returned_at = timezone.now()
            self.save(update_fields=["returned_at"])
