# -*- coding: utf-8 -*-
"""
lesson_02：Core 层 select() 表达式 —— 用 Python 结构化地拼 SQL
运行：python lesson_02_core_select.py
重点：方法链与 Django QuerySet 的手感几乎一致；每条语句都能 print 出真实 SQL。
"""
import os

from sqlalchemy import (
    create_engine, MetaData, Table, select, func, case, cast, Numeric, String,
)

DB_URL = os.environ.get("LEARN_PG_URL",
                        "postgresql+psycopg2://postgres:postgresql@localhost:5432/learn_pg")
engine = create_engine(DB_URL, echo=False)     # 本课关掉 echo，用我们自己的 pretty 函数展示 SQL

# ---------- 1. 反射：从真实库加载表结构（不用手写列定义） ----------
metadata = MetaData()
with engine.connect() as conn:
    orders: Table = Table("orders", metadata, autoload_with=conn)
    order_items: Table = Table("order_items", metadata, autoload_with=conn)
    products: Table = Table("products", metadata, autoload_with=conn)
    users: Table = Table("users", metadata, autoload_with=conn)

def pretty(stmt) -> None:
    """打印编译后的 SQL（literal_binds 把参数内联进去，方便复制到 psql 调试）"""
    print(stmt.compile(compile_kwargs={"literal_binds": True}))

# ---------- 2. 基础链：where / order_by / limit（≈ filter / order_by / 切片） ----------
stmt = (
    select(orders.c.id, orders.c.user_id, orders.c.total, orders.c.created_at)
    .where(orders.c.status == "pending")            # filter(status='pending')
    .where(orders.c.total > 10000)                  # 链式 where = AND
    .order_by(orders.c.total.desc())                # order_by('-total')
    .limit(5)                                       # [:5]
)
pretty(stmt)
with engine.connect() as conn:
    for row in conn.execute(stmt):
        print("  ", row.id, row.user_id, row.total, str(row.created_at)[:19])

# ---------- 3. 聚合：group_by / having / 聚合函数打标签 ----------
# 等价 SQL：SELECT status, count(*), sum(total) ... GROUP BY status HAVING count(*) > 100000
stmt = (
    select(
        orders.c.status,
        func.count().label("cnt"),                  # Count
        func.sum(orders.c.total).label("amount"),   # Sum
    )
    .group_by(orders.c.status)
    .having(func.count() > 100000)
    .order_by(func.sum(orders.c.total).desc())
)
pretty(stmt)
with engine.connect() as conn:
    print()
    for row in conn.execute(stmt):
        print(f"   {row.status:10} {row.cnt:>8} {row.amount:>16,.2f}")

# ---------- 4. JOIN：多表条件聚合 ----------
# 阶段 1 题 7 的 Core 版：每个分类的商品数与均价
stmt = (
    select(
        products.c.category_id,
        func.count().label("cnt"),
        func.round(func.avg(products.c.price).cast(Numeric(12, 2)), 2).label("avg_price"),
    )
    .group_by(products.c.category_id)
    .order_by(func.count().desc())
    .limit(5)
)
pretty(stmt)
with engine.connect() as conn:
    print()
    for row in conn.execute(stmt):
        print(f"   分类 {row.category_id}: {row.cnt} 件, 均价 {row.avg_price}")

# ---------- 5. 表达式：case / cast / in_ / between / like ----------
stmt = select(
    users.c.status,
    func.count().label("cnt"),
).group_by(users.c.status)

vip_cond = case((orders.c.total > 20000, "大额"), else_="普通")
stmt2 = (
    select(vip_cond.label("bucket"), func.count().label("cnt"))
    .group_by("bucket")
)
pretty(stmt2)
with engine.connect() as conn:
    print()
    for row in conn.execute(stmt2):
        print(f"   {row.bucket}: {row.cnt}")

# 其他常用条件写法（打印 SQL 体会对应关系）：
demo = select(orders.c.id).where(
    orders.c.user_id.in_([1, 2, 3]),                # __in
    orders.c.status.not_in(["cancelled"]),          # exclude
    orders.c.total.between(100, 200),               # __range
    orders.c.created_at >= func.now() - func.make_interval(0, 0, 0, 7),  # __gte
)
pretty(demo)

# ---------- 6. 复用与组合：语句是值，可以套娃（子查询） ----------
inner = (
    select(orders.c.user_id, func.sum(orders.c.total).label("spent"))
    .where(orders.c.status == "completed")
    .group_by(orders.c.user_id)
    .subquery()                                     # 派生表（≈ 阶段 2 的 WITH）
)
stmt = (
    select(users.c.nickname, inner.c.spent)
    .join(inner, users.c.id == inner.c.user_id)
    .order_by(inner.c.spent.desc())
    .limit(5)
)
pretty(stmt)
with engine.connect() as conn:
    print()
    for row in conn.execute(stmt):
        print(f"   {row.nickname}: {row.spent:,.2f}")

print("\n小结：Core 层 = '结构可控的 SQL'，复杂分析查询（窗口函数等）直接 text() 也完全合理。")
