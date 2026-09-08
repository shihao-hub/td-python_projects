"""SQLAlchemy 2.0 声明式映射：把 catalog 的 Django 模型逐一「翻译」过来。

核心前提：**表由 Django 迁移建、SQLAlchemy 只做映射**（共库对照）。
所以这里的 mapped_column 只描述「已存在的列」，不负责建表（不跑 create_all）。

Django 模型 → SQLAlchemy 映射的对照速查：
- models.Model              ↔ class Base(DeclarativeBase) + Mapped[]/mapped_column()
- models.CharField          ↔ String(length)；TextChoices 枚举 ↔ StrEnum + Enum(native_enum=False)
- models.DecimalField       ↔ Numeric(precision, scale)
- GeneratedField(含税价)    ↔ Computed("...")（STORED 生成列，INSERT 由 DB 算，只读）
- db_default=Now()          ↔ server_default=func.now()（INSERT 缺省由 DB 填）
- ForeignKey(DB_CASCADE)    ↔ ForeignKey(..., ondelete="CASCADE")（级联下沉数据库，两边一致：
                              Django 6.1 的 DB_CASCADE 生成的就是 ON DELETE CASCADE 子句）
- related_name="books"      ↔ relationship(back_populates="author")（显式双向，无魔法注册）
- ManyToManyField(through)  ↔ 显式关联表类 BookGenre + viewonly 便捷集合
- Meta.constraints          ↔ UniqueConstraint(name=...)（约束名两边对得上）
- Meta.ordering             ↔ 【没有对应物】SQLAlchemy 查询必须显式 order_by，见文档
- @property（仅实例用）     ↔ hybrid_property（实例 + 查询两用，一个定义顶 Django 的 property+Q）
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from django.urls import reverse
from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Django BigAutoField 在 PostgreSQL 下建的是 bigint；SQLite 方言里
# INTEGER PRIMARY KEY 才有 rowid 自增语义，所以 SQLite 降回 Integer
BigPK = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    """所有映射的基类；Django 里这层是隐式的（models.Model 背后的 metaclass）。"""


class Status(enum.StrEnum):
    """对照 models.TextChoices：数据库存的就是这些字符串。

    Django 的 choices 附带中文 label（admin/表单展示用），那部分能力属于
    Django 生态；SQLAlchemy 侧只需要「值 ⇄ 枚举」的转换。
    """

    DRAFT = "DRAFT"
    AVAILABLE = "AVAILABLE"
    BORROWED = "BORROWED"
    RETIRED = "RETIRED"


# 对照 Django TextChoices 的中文 label：get_status_display() 查这张表，
# 让 DRF 的 BookSerializer（source="get_status_display"）无需改动即可序列化 SaBook
STATUS_LABELS: dict[str, str] = {
    Status.DRAFT: "草稿",
    Status.AVAILABLE: "可借",
    Status.BORROWED: "已借出",
    Status.RETIRED: "已下架",
}


class Genre(Base):
    __tablename__ = "catalog_genre"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    def __repr__(self) -> str:
        return f"Genre(id={self.id}, name={self.name!r})"

    def __str__(self) -> str:
        # 对齐 Django Genre.__str__ —— BookSerializer 的 StringRelatedField 依赖
        return self.name


class Author(Base):
    __tablename__ = "catalog_author"
    # 对照 Meta.constraints = [UniqueConstraint(fields=[...], name="unique_author_name")]
    __table_args__ = (UniqueConstraint("first_name", "last_name", name="unique_author_name"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    about: Mapped[str] = mapped_column(Text)

    # related_name="books" 的镜像。注意：不带 ORM 级 cascade ——
    # 级联删除和 Django 的 DB_CASCADE 一样下沉到数据库（ON DELETE CASCADE）
    books: Mapped[list["Book"]] = relationship(back_populates="author")

    # 对照 Author.full_name property；hybrid 让同一个定义还能出现在 where 里
    @hybrid_property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @full_name.inplace.expression  # type: ignore[no-redef]
    @classmethod
    def full_name(cls) -> object:
        # 类上下文（查询）下返回 SQL 表达式 —— 一个定义两用
        return cls.first_name.concat(" ").concat(cls.last_name)

    def __repr__(self) -> str:
        return f"Author(id={self.id}, name={self.last_name} {self.first_name})"

    def __str__(self) -> str:
        # 对齐 Django Author.__str__（"姓 名"）—— serializer 依赖的显示格式只此一处
        return f"{self.last_name} {self.first_name}".strip()


class Book(Base):
    __tablename__ = "catalog_book"
    # 对照 Meta.indexes = [models.Index(fields=["status"], name="book_status_idx")]
    __table_args__ = (Index("book_status_idx", "status"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("catalog_author.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text)
    isbn: Mapped[str] = mapped_column(String(20), default="")
    price: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"))
    # 对照 GeneratedField：STORED 生成列 —— INSERT 排除该列，读取由 DB 计算返还
    price_with_tax: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), Computed("(price * 1.09)", persisted=True)
    )
    # 对照 status = CharField(choices=Status.choices, default=Status.AVAILABLE)：
    # native_enum=False → 普通 varchar；values_callable → 存 .value（与 Django 一致）；
    # 读回来的就是 Status 枚举成员，而不是裸字符串
    status: Mapped[Status] = mapped_column(
        Enum(Status, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        default=Status.AVAILABLE,
    )
    # 对照 db_default=Now()：INSERT 不带该列时由数据库填
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now())

    author: Mapped[Author] = relationship(back_populates="books")
    # 对照显式 through 的 M2M：关联表有自己的 ORM 类 BookGenre（写操作走它），
    # 这里再给一个 viewonly 便捷集合模拟 book.genres 的读体验 —— 与
    # BookGenre.book/genre 的关系重叠，所以禁止经它写（否则两套关系会打架）
    genres: Mapped[list[Genre]] = relationship(
        secondary="catalog_bookgenre",
        primaryjoin="Book.id == BookGenre.book_id",
        secondaryjoin="Genre.id == BookGenre.genre_id",
        viewonly=True,
        order_by="Genre.name",
    )
    borrows: Mapped[list["BorrowRecord"]] = relationship(back_populates="book")

    # 对照 Book.is_borrowable property；hybrid 版可直接 where(Book.is_borrowable)
    @hybrid_property
    def is_borrowable(self) -> bool:
        return self.status == Status.AVAILABLE

    @is_borrowable.inplace.expression  # type: ignore[no-redef]
    @classmethod
    def is_borrowable(cls) -> object:
        return cls.status == Status.AVAILABLE

    # ---- 协议兼容方法：让 DRF 的 BookSerializer（读方向鸭子类型）直接吃 SaBook ----

    def get_status_display(self) -> str:
        """对照 Django choices 自动生成的 get_status_display —— serializer 的
        source="get_status_display" 按名调用，两个 ORM 均满足此协议。"""
        return STATUS_LABELS.get(self.status, str(self.status))

    def get_absolute_url(self) -> str:
        """对照 Django 模型的 get_absolute_url（DRY 的 URL 单点定义）。

        有趣之处：SQLAlchemy 对象调 Django 的 reverse() —— URL 命名空间
        与 ORM 引擎无关，两套模型共享同一套路由定义。"""
        return reverse("catalog:book-detail", args=[self.slug])

    def __repr__(self) -> str:
        return f"Book(id={self.id}, title={self.title!r}, status={self.status})"


class BookGenre(Base):
    """对照显式 through 模型 BookGenre（Django 6.1 要求 DB 级级联链上显式声明）。

    book/genre 用单向 relationship：Django 的 related_name="book_genres"
    反向集合在 SQLAlchemy 里就是再声明一个 relationship —— 这里为避免与
    Book.genres（viewonly）重复，只保留正向。
    """

    __tablename__ = "catalog_bookgenre"
    __table_args__ = (UniqueConstraint("book_id", "genre_id", name="unique_book_genre"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("catalog_book.id", ondelete="CASCADE"))
    genre_id: Mapped[int] = mapped_column(ForeignKey("catalog_genre.id", ondelete="CASCADE"))

    book: Mapped[Book] = relationship()
    genre: Mapped[Genre] = relationship()

    def __repr__(self) -> str:
        return f"BookGenre(book_id={self.book_id}, genre_id={self.genre_id})"


class LabUser(Base):
    """只映射 auth_user 用到的两列 —— SQLAlchemy 支持「部分列映射」。

    对照 Django 的 related_name="borrow_records"（settings.AUTH_USER_MODEL 反向）。
    """

    __tablename__ = "auth_user"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)

    borrow_records: Mapped[list["BorrowRecord"]] = relationship(back_populates="borrower")

    def __repr__(self) -> str:
        return f"LabUser(id={self.id}, username={self.username!r})"


class BorrowRecord(Base):
    __tablename__ = "catalog_borrowrecord"
    __table_args__ = (
        # 对照部分唯一约束 UniqueConstraint(condition=Q(returned_at__isnull=True))：
        # SQLAlchemy 称为「条件索引」，sqlite_where / postgresql_where 按后端出 DDL；
        # 真实表由 Django 迁移建，这里是对等描述（约束名与 Django 生成的完全一致）
        Index(
            "unique_active_borrow_per_book",
            "book_id",
            unique=True,
            sqlite_where=text("returned_at IS NULL"),
            postgresql_where=text("returned_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("catalog_book.id", ondelete="CASCADE"))
    borrower_id: Mapped[int] = mapped_column(ForeignKey("auth_user.id", ondelete="CASCADE"))
    borrowed_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now())
    due_date: Mapped[date] = mapped_column(Date)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(), default=None)

    book: Mapped[Book] = relationship(back_populates="borrows")
    borrower: Mapped[LabUser] = relationship(back_populates="borrow_records")

    # 对照 is_active / is_overdue property：hybrid 使 where(BorrowRecord.is_active) 直接可用
    @hybrid_property
    def is_active(self) -> bool:
        return self.returned_at is None

    @is_active.inplace.expression  # type: ignore[no-redef]
    @classmethod
    def is_active(cls) -> object:
        return cls.returned_at.is_(None)

    @hybrid_property
    def is_overdue(self) -> bool:
        return self.returned_at is None and self.due_date < date.today()

    @is_overdue.inplace.expression  # type: ignore[no-redef]
    @classmethod
    def is_overdue(cls) -> object:
        # 查询上下文下翻译成 SQL：AND 要显式括号（& 优先级高于比较会踩坑）
        return cls.returned_at.is_(None) & (cls.due_date < func.current_date())

    def __repr__(self) -> str:
        return (
            f"BorrowRecord(id={self.id}, book_id={self.book_id}, "
            f"borrower_id={self.borrower_id}, returned_at={self.returned_at})"
        )
