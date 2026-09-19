# 阶段 5：SQLAlchemy 2.0（纯 SQLAlchemy，不绑定 Web 框架）

> 目标：以「写 SQL」为主线掌握 SQLAlchemy 2.0——`text()` 参数绑定起步（防注入），到 `select()` 表达式，再到 ORM 与 Session，最后亲手制造并消除一次 N+1。
> 每课都是可直接运行的脚本：`python lesson_01_engine_text.py`（会打印 SQL 和结果）。
> 连接：默认 `postgresql+psycopg2://postgres:postgresql@localhost:5432/learn_pg`，可用环境变量 `LEARN_PG_URL` 覆盖。

```powershell
pip install -r requirements.txt
python lesson_01_engine_text.py
```

## 0. 两个层次：Core 和 ORM

| | Core（SQL 表达式层） | ORM（对象关系映射层） |
|---|---|---|
| 心智模型 | "用 Python 拼 SQL" | "用 Python 对象表示行" |
| 组成 | `Engine`、`Table`、`select()/insert()...` | `DeclarativeBase`、`Session`、`relationship` |
| 适合 | 报表、批量、DBA 式操作、SQL 完全可控 | 业务模型、常规 CRUD、带关系的领域逻辑 |
| 关系 | ORM **建立在 Core 之上** | `session.execute(select(...))` 拿到的就是 Core 语句 |

Django 对照：你熟悉的 `Model.objects` ≈ ORM 层；Django 没有暴露的"拼 SQL 层"≈ Core 层。学习路线：**text()（裸 SQL）→ Core（结构化 SQL）→ ORM（对象）**，逐层抽象，出问题时能逐层降级排查。

## 1. Engine 与连接池

```python
from sqlalchemy import create_engine

engine = create_engine(DB_URL, echo=True, pool_size=5, max_overflow=10, pool_pre_ping=True)
```

- `create_engine` **惰性连接**：创建时不连库，第一次用时才建连接。
- 连接池默认 `QueuePool`：`pool_size` 常驻 + `max_overflow` 临时溢出，连接归还复用（Django 的 CONN_MAX_AGE 是"连完保持"，语义不同）。
- `pool_pre_ping=True`：取连接前 ping 一下，防"连接已被数据库掐掉"（长闲置后报错的经典解法）。
- `echo=True`：打印所有 SQL——**学习/调试期永远开着**。
- Engine 是进程级单例（一个库一个），`conn = engine.connect()` 是一次借用，用完（context manager）归还池里，不是物理关闭。

## 2. text()：写裸 SQL，参数绑定防注入

```python
from sqlalchemy import text

with engine.connect() as conn:
    # :name 是绑定参数，值单独传——SQL 结构与数据分离，注入不可能
    r = conn.execute(text("SELECT id, nickname FROM users WHERE status = :status LIMIT :n"),
                     {"status": "banned", "n": 5})
    for row in r:
        print(row.id, row.nickname)

    r2 = conn.execute(text("SELECT count(*) FROM orders WHERE user_id = :uid"), {"uid": 42})
    print(r2.scalar())          # scalar() 取第一行第一列

    rows = conn.execute(text("SELECT status, count(*) FROM orders GROUP BY status")).mappings().all()
    print(rows[0]["count"])     # mappings() → 可按列名取的 dict 式行
```

**为什么禁止 f-string 拼 SQL**（lesson_01 里有现场演示）：

```python
# ❌ 注入现场：输入 "x' OR '1'='1" 直接改写 SQL 语义
conn.execute(text(f"SELECT * FROM users WHERE nickname = '{name}'"))
# ✅ 绑定参数：输入永远是"值"，永远不可能变成 SQL 的一部分
conn.execute(text("SELECT * FROM users WHERE nickname = :name"), {"name": name})
```

Django 对照：`.raw()` / `extra(where=...)` 里手拼 SQL 的风险完全相同；参数绑定对应 `.raw(..., params=[...])`。

## 3. Core：select() 表达式

```python
from sqlalchemy import table, column, select, func

orders = table("orders", column("user_id"), column("status"), column("total"))  # 轻量声明
stmt = (
    select(orders.c.user_id, func.count().label("cnt"), func.sum(orders.c.total).label("amount"))
    .where(orders.c.status == "completed")
    .group_by(orders.c.user_id)
    .having(func.count() > 500)
    .order_by(func.sum(orders.c.total).desc())
    .limit(5)
)
with engine.connect() as conn:
    for row in conn.execute(stmt):
        print(row.user_id, row.cnt, row.amount)
```

- 方法链和 Django QuerySet 像极了：`where/order_by/limit` ↔ `filter/order_by/切片`；区别是 **SQLAlchemy 的链是立即生成 SQL 的不可变对象**（QuerySet 也是惰性的，这点像；但 SQLAlchemy 语句执行才显式）。
- 真实项目用 `Table(...,MetaData())` 或反射加载真实结构（lesson_02 演示反射），列类型、FK 都可用。
- 打印编译出的 SQL：`print(stmt.compile(compile_kwargs={"literal_binds": True}))`。

## 4. ORM：模型与查询

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str]
    nickname: Mapped[str]
    orders: Mapped[list["Order"]] = relationship(back_populates="user")

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user: Mapped["User"] = relationship(back_populates="orders")
```

- `Mapped[str]` 类型注解即列类型（`Optional[...]` = nullable）——2.0 风格，不再需要 `Column(String(50))`。
- 模型**不建表**（本课程库已存在，模型只做映射）；要建表是 `Base.metadata.create_all(engine)`。
- 查询统一 2.0 API（`Query.get()`/`session.query()` 已淘汰）：

```python
with Session(engine) as session:
    users = session.scalars(select(User).where(User.status == "banned").limit(5)).all()
    u = session.get(User, 42)                 # 主键查询（≈ User.objects.get(pk=42)，但找不到返回 None 不抛错）
    stmt = select(Order).where(Order.user_id == 42).order_by(Order.created_at.desc()).limit(3)
    orders = session.scalars(stmt).all()
```

## 5. Session：生命周期（Django 用户最大的心智迁移点）

**QuerySet ≠ Session**：

| | Django QuerySet | SQLAlchemy Session |
|---|---|---|
| 是什么 | 惰性 SQL 构造器，每次迭代执行一次 SQL | **一个事务范围 + 对象缓存（Identity Map）** |
| 缓存 | 无（重复迭代 = 重复查询） | 同一主键在一个 Session 里只有一个 Python 对象 |
| 写入 | `.save()` 显式 | **commit 时统一 flush**，dirty 对象自动跟踪 |
| 存活 | 即用即弃 | 必须显式关闭（context manager），长寿命 Session 是 bug 之源 |

```python
# 标准姿势：with 块 = 一个事务，退出自动 commit/rollback+close
with Session(engine) as session:
    u = session.get(User, 1)
    u.nickname = "new_name"
    session.commit()          # 之前不需要显式 save：Session 一直在跟踪改动（dirty）

# 需要"事务 + 自动关闭"一步到位：
with Session(engine) as session, session.begin():
    ...
```

- **Identity Map**：`session.get(User, 1) is session.get(User, 1)` → `True`（同一对象，不发两次 SQL）。
- `expire_on_commit`：commit 后对象属性默认过期，下次访问重新 SELECT（在 web 请求里序列化前先取值，或 `session.expire_on_commit = False`）。
- 惰性加载与 Session 绑定：**Session 关了再访问 `user.orders` 直接报 `DetachedInstanceError`**——lesson_05 的 N+1 实验会撞见它。

## 6. 关系加载策略：N+1 的防与治（lesson_05 实战）

默认 `relationship` 是 **lazy="select"**：访问 `user.orders` 时**每个用户发一条 SQL**。取 100 个用户再逐个访问 orders = 1 + 100 = 101 条查询——这就是 N+1。

```python
from sqlalchemy.orm import selectinload, joinedload

# 预加载方案 A：selectinload —— 两条 SQL（用户一条 + WHERE user_id IN (...) 一条）
users = session.scalars(select(User).options(selectinload(User.orders)).limit(100)).all()

# 预加载方案 B：joinedload —— 一条 SQL（LEFT JOIN，结果去重后映射）
users = session.scalars(select(User).options(joinedload(User.orders)).limit(100)).all()
```

Django 对照（一一对应，语义几乎相同）：

| SQLAlchemy | Django | SQL 形态 |
|---|---|---|
| 默认 lazy="select" | 默认（不调用 select_related/prefetch_related） | N+1 |
| `joinedload` | `select_related` | 一条 JOIN |
| `selectinload` | `prefetch_related` | 两条 + IN |
| `subqueryload` | （无直接对应） | 两条 + 子查询 |

选型经验：**多对一/一对一用 joinedload（JOIN 不膨胀）；一对多/多对多用 selectinload（IN 比 JOIN 便宜且不重复行）**。

## 7. 写入：增删改与批量

```python
with Session(engine) as session:
    session.add(User(email="a@b.c", nickname="a"))
    session.add_all([User(email=f"u{i}@b.c", nickname=f"u{i}") for i in range(100)])  # ≈ bulk_create
    session.commit()                                   # 逐条 INSERT（100 条）

    # 真正的批量：一条多值 INSERT
    from sqlalchemy import insert
    session.execute(insert(User), [{"email": f"x{i}@b.c", "nickname": f"x{i}"} for i in range(100)])
    session.commit()

    # UPDATE/DELETE 也是"先查后改"或表达式直接改（≈ queryset.update()）
    session.execute(update(User).where(User.status == "banned").values(status="inactive"))
    session.execute(delete(User).where(User.nickname.like("x%")))
    session.commit()
```

UPSERT（PG 方言）：

```python
from sqlalchemy.dialects.postgresql import insert
stmt = insert(User).values(id=1, nickname="new").on_conflict_do_update(index_elements=["id"], set_={"nickname": "new"})
session.execute(stmt)     # 底层就是阶段 3 学的 ON CONFLICT
```

## 8. 常见坑清单

| 坑 | 症状 | 解法 |
|---|---|---|
| 拼 SQL 字符串 | 注入 | 一律 `text()` + `:param` |
| Session 活太久 | 脏数据、连接耗尽、DetachedInstanceError | 一个工作单元一个 Session，用 `with` |
| 忘 commit | 改动消失 | `with` + 显式 commit；`session.begin()` |
| N+1 | 页面几十条 SQL | selectinload/joinedload（lesson_05） |
| commit 后访问属性 | 意外 SELECT 或 Detached 错误 | expire_on_commit=False / 先取值 |
| `session.get` 找不到 | 返回 None（Django get 会抛 DoesNotExist） | 判 None 或用 `.one()`（找不到抛 NoResultFound） |
| sqlite 内存里的 datetime/行为差异 | 本地测试过了、PG 上翻车 | 测试也用 PG（docker/本地起一个都行） |
| engine 创建多次 | 连接池各自为政、连接数暴涨 | 模块级单例 |

## 9. 练习

`exercises.py`（8 个 TODO 函数，跑 `python exercises.py` 自查，每个函数有期望输出提示）。

## 10. 自测清单

- [ ] Core 和 ORM 的分层关系？各自适合什么场景？
- [ ] 绑定参数为什么能防注入（原理：结构 vs 数据分离）？
- [ ] Engine / 连接池 / connect() 三者关系？pool_pre_ping 解决什么？
- [ ] Session 和 QuerySet 的三个核心区别？
- [ ] Identity Map 是什么？`get` 两次同一主键发几次 SQL？
- [ ] N+1 怎么产生？selectinload / joinedload 的 SQL 形态和选型原则？
- [ ] DetachedInstanceError 什么时候出现？
- [ ] 批量插入的两种方式差异？UPSERT 在 SQLAlchemy 里怎么写？
