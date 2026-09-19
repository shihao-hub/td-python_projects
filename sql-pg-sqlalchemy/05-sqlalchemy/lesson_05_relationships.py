# -*- coding: utf-8 -*-
"""
lesson_05：关系加载策略 —— 亲手制造 N+1，再亲手消灭它
运行：python lesson_05_relationships.py
重点：lazy="select" 默认就是 N+1；selectinload/joinedload 对照 Django 的
      prefetch_related/select_related。
"""
import os
import time

from sqlalchemy import create_engine, select, event
from sqlalchemy.orm import Session, selectinload, joinedload

from lesson_03_orm_model import Base, User, Order

DB_URL = os.environ.get("LEARN_PG_URL",
                        "postgresql+psycopg2://postgres:postgresql@localhost:5432/learn_pg")
engine = create_engine(DB_URL, echo=False)

# ---------- 0. SQL 计数器：监听每一条发出去的 SQL ----------
query_count = 0

@event.listens_for(engine, "before_cursor_execute")
def count_sql(conn, cursor, statement, parameters, context, executemany):
    global query_count
    query_count += 1

def reset_counter():
    global query_count
    query_count = 0

# ---------- 1. N+1 现场：默认懒加载 ----------
# 需求：展示 100 个用户各自的下单数与最近一单时间
N = 100

def run_lazy(session: Session) -> None:
    users = session.scalars(select(User).order_by(User.id).limit(N)).all()
    for u in users:
        _ = u.orders          # 每个用户触发一条 SELECT ... WHERE user_id = ?（懒加载）

with Session(engine) as session:
    reset_counter()
    t0 = time.perf_counter()
    run_lazy(session)
    lazy_time = time.perf_counter() - t0
    lazy_queries = query_count
    print(f"懒加载:   {lazy_queries:>4} 条 SQL, 耗时 {lazy_time*1000:8.1f} ms")
    # 预期输出：101 条 SQL（1 查用户 + 100 查订单），这就是 N+1
    #
    # 注意：orders.user_id 上【没有索引】（schema 刻意没建，PG 外键不带索引），
    # 所以这 100 条每条都是百毫秒级的全表扫 —— N+1 之外还叠加了缺索引惩罚。
    # 如果你在阶段 4 的 lab02 建过 (user_id...) 索引并保留了，耗时会小很多，
    # 但 101 条 SQL 的模式不变 —— 这正是"数量问题"和"单条质量问题"要分开看的例子。

# ---------- 2. selectinload：两条 SQL 解决（≈ Django prefetch_related） ----------
def run_selectin(session: Session) -> None:
    users = session.scalars(
        select(User).options(selectinload(User.orders)).order_by(User.id).limit(N)
    ).all()
    for u in users:
        _ = u.orders          # 已预加载，不再触发 SQL

with Session(engine) as session:
    reset_counter()
    t0 = time.perf_counter()
    run_selectin(session)
    selectin_time = time.perf_counter() - t0
    print(f"selectin: {query_count:>4} 条 SQL, 耗时 {selectin_time*1000:8.1f} ms")
    # 预期：2 条 SQL —— ① 查 100 个用户；② SELECT ... FROM orders WHERE user_id IN (1,2,...,100)

# ---------- 3. joinedload：一条 SQL（≈ Django select_related，用于一对多要小心） ----------
def run_joined(session: Session) -> None:
    # 一对多 JOIN 会把父行复制多份，2.0 要求显式 .unique() 让 ORM 去重还原对象
    users = session.scalars(
        select(User).options(joinedload(User.orders)).order_by(User.id).limit(N)
    ).unique().all()
    for u in users:
        _ = u.orders

with Session(engine) as session:
    reset_counter()
    t0 = time.perf_counter()
    run_joined(session)
    joined_time = time.perf_counter() - t0
    print(f"joined:   {query_count:>4} 条 SQL, 耗时 {joined_time*1000:8.1f} ms")
    # 预期：1 条 SQL（LEFT JOIN + ORM 去重映射）。
    # 一对多场景 JOIN 会把父行复制多份（100 用户 × 人均 ~20 单 = ~2000 行中间结果），
    # 所以经验法则：一对多/多对多用 selectinload，多对一/一对一用 joinedload。

# ---------- 4. 倒过来的方向：订单页取 user（多对一） ----------
with Session(engine) as session:
    reset_counter()
    t0 = time.perf_counter()
    orders = session.scalars(
        select(Order).order_by(Order.id.desc()).limit(50).options(joinedload(Order.user))
    ).all()
    rows = [(o.id, o.user.nickname) for o in orders]      # 不再触发额外 SQL
    print(f"\n订单→用户（多对一, joinedload）: {query_count} 条 SQL, {len(rows)} 行")

# ---------- 5. DetachedInstanceError 现场 ----------
with Session(engine) as session:
    u = session.scalars(select(User).limit(1)).one()
try:
    print(u.orders)          # Session 已关闭，懒加载无处发 SQL
except Exception as e:
    print(f"\nSession 关闭后访问关系 → {type(e).__name__}（修复：Session 内取数据，"
          f"或提前 selectinload）")

# ---------- 6. 对比结论 ----------
print(f"""
结论（100 用户场景）：
  懒加载      {lazy_queries} 条 SQL   {lazy_time*1000:8.1f} ms
  selectin      2 条 SQL   {selectin_time*1000:8.1f} ms
  joined        1 条 SQL   {joined_time*1000:8.1f} ms
对照 Django：
  懒加载 N+1            → 不处理时的默认行为
  selectinload(...)     → prefetch_related(...)   一对多/多对多首选
  joinedload(...)       → select_related(...)     多对一/一对一首选
另外别忘了阶段 4 的教训：orders.user_id 至今没有索引——给真实业务建上它，
上面三种方式的每一条 SQL 都会再快一个量级（lab02 已验证）。
""")
