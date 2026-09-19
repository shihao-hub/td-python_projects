# -*- coding: utf-8 -*-
"""
阶段 5 练习：8 个 TODO
运行：python exercises.py   —— 自动逐题检查，全部完成后应全绿。
说明：把函数体里的 `raise NotImplementedError` 换成你的实现；卡住了看函数 docstring 的提示，
     全部做完后可对照文件底部的「参考答案」注释段自查。
"""
import os
from datetime import datetime

from sqlalchemy import create_engine, event, select, func, text
from sqlalchemy.orm import Session, selectinload

from lesson_03_orm_model import Base, User, Order   # 复用 lesson_03 定义的模型

DB_URL = os.environ.get("LEARN_PG_URL",
                        "postgresql+psycopg2://postgres:postgresql@localhost:5432/learn_pg")
engine = create_engine(DB_URL, echo=False)


# ---------- 题 1 ----------
def ex01_text_query(session: Session, status: str) -> int:
    """用 text() + 绑定参数返回指定状态的订单数（pending 应为 20000）。
    提示：session.execute(text("SELECT count(*) FROM orders WHERE status = :s"), {"s": ...}).scalar()"""
    raise NotImplementedError


# ---------- 题 2 ----------
def ex02_core_group(session: Session) -> dict[str, int]:
    """用 select() 按状态统计订单数，返回 {status: count} 字典（应 5 个键，completed=580000）。
    提示：select(Order.status, func.count()).group_by(Order.status)"""
    raise NotImplementedError


# ---------- 题 3 ----------
def ex03_top_categories(session: Session) -> list[tuple[int, int]]:
    """商品数最多的前 3 个分类：返回 [(category_id, 商品数), ...]。
    提示：select + group_by + order_by(func.count().desc()).limit(3)"""
    raise NotImplementedError


# ---------- 题 4 ----------
def ex04_latest_banned(session: Session) -> list[User]:
    """最近注册的 5 个 banned 用户（按 created_at 倒序），返回 ORM 对象列表。
    提示：session.scalars(select(User).where(...).order_by(...).limit(5)).all()"""
    raise NotImplementedError


# ---------- 题 5 ----------
def ex05_recent_orders(session: Session, user_id: int = 42, n: int = 3) -> list[Order]:
    """某用户最近 n 笔订单（按 created_at 倒序）。两种写法任选：
    ① select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(n)
    ② session.get(User, user_id).orders 排序后切片（注意这是懒加载）"""
    raise NotImplementedError


# ---------- 题 6 ----------
def ex06_monthly_2026(session: Session) -> list[tuple[str, int]]:
    """2026 年每月订单数：返回 [("2026-01", n), ...]，应恰好 9 个月。
    提示：func.to_char(func.date_trunc('month', Order.created_at), 'YYYY-MM') + group_by"""
    raise NotImplementedError


# ---------- 题 7 ----------
def ex07_create_update_delete(session: Session) -> bool:
    """完整写入流程：① 新增用户 email='ex07@test', nickname='ex07', status='active',
    created_at=now() ② flush 后把 nickname 改为 'ex07_v2' ③ commit ④ 重新 get 验证昵称
    已是 ex07_v2 ⑤ 删除该用户并 commit ⑥ get 不到则返回 True。"""
    raise NotImplementedError


# ---------- 题 8 ----------
def ex08_no_n_plus_one(session: Session, n_users: int = 20) -> int:
    """修复 N+1：取前 n_users 个用户并返回他们的订单总数，但不许产生 N+1。
    检查器会统计你执行期间发出的 SQL 条数（必须 <= 3）。
    提示：select(User).options(selectinload(User.orders))，然后 sum(len(u.orders))"""
    raise NotImplementedError


# ============================================================
# 自动检查（不要改下面的代码）
# ============================================================
query_count = 0

@event.listens_for(engine, "before_cursor_execute")
def _count_sql(conn, cursor, statement, parameters, context, executemany):
    global query_count
    query_count += 1


def check(name, fn, expect):
    global query_count
    query_count = 0
    try:
        with Session(engine) as session:
            got = fn(session)
        ok = expect(got)
    except NotImplementedError:
        print(f"[     ] 题 {name}: 未实现")
        return False
    except Exception as e:
        print(f"[ ✘✘✘ ] 题 {name}: 抛异常 {type(e).__name__}: {e}")
        return False
    mark = "[ ✓✓✓ ]" if ok else "[ ✘✘✘ ]"
    print(f"{mark} 题 {name}: {'通过' if ok else '结果不符合预期'} → {got if not isinstance(got, list) else str(got)[:80]}")
    return ok


def main() -> None:
    results = []

    results.append(check(
        "1 text() 绑定参数",
        lambda s: ex01_text_query(s, "pending"),
        lambda v: v == 20000,
    ))
    results.append(check(
        "2 分组统计",
        lambda s: ex02_core_group(s),
        lambda d: isinstance(d, dict) and d.get("completed") == 580000 and len(d) == 5,
    ))
    results.append(check(
        "3 分类 Top3",
        lambda s: ex03_top_categories(s),
        lambda v: isinstance(v, list) and len(v) == 3 and v[0][1] == 358,
    ))
    results.append(check(
        "4 ORM 查询",
        lambda s: ex04_latest_banned(s),
        lambda v: isinstance(v, list) and len(v) == 5
                  and all(u.status == "banned" for u in v),
    ))
    results.append(check(
        "5 用户订单",
        lambda s: ex05_recent_orders(s, 42, 3),
        lambda v: isinstance(v, list) and len(v) == 3
                  and all(o.user_id == 42 for o in v)
                  and v[0].created_at >= v[1].created_at >= v[2].created_at,
    ))
    results.append(check(
        "6 月度统计",
        lambda s: ex06_monthly_2026(s),
        lambda v: isinstance(v, list) and len(v) == 9 and v[0][0] == "2026-01",
    ))
    results.append(check(
        "7 增改删",
        lambda s: ex07_create_update_delete(s),
        lambda v: v is True,
    ))

    # 题 8：单独跑，检查 SQL 条数
    global query_count
    try:
        with Session(engine) as session:
            query_count = 0
            total = ex08_no_n_plus_one(session, 20)
            used = query_count
        ok = isinstance(total, int) and total > 0 and used <= 3
        print(f"{'[ ✓✓✓ ]' if ok else '[ ✘✘✘ ]'} 题 8 修复 N+1: 订单总数={total}, 发出 SQL={used} 条（要求<=3）")
        results.append(ok)
    except NotImplementedError:
        print("[     ] 题 8 修复 N+1: 未实现")
        results.append(False)

    print(f"\n通过 {sum(results)}/{len(results)} 题")


if __name__ == "__main__":
    main()


# ============================================================
# 参考答案（先自己做完再看！）
# ============================================================
# 题 1:
#   return session.execute(
#       text("SELECT count(*) FROM orders WHERE status = :s"), {"s": status}
#   ).scalar()
#
# 题 2:
#   stmt = select(Order.status, func.count()).group_by(Order.status)
#   return {status: cnt for status, cnt in session.execute(stmt)}
#
# 题 3（lesson_03 没定义 Product 模型，用反射或 text() 都行；反射版）:
#   from sqlalchemy import MetaData, Table
#   products = Table("products", MetaData(), autoload_with=session.connection())
#   stmt = (
#       select(products.c.category_id, func.count())
#       .group_by(products.c.category_id)
#       .order_by(func.count().desc())
#       .limit(3)
#   )
#   return [(cid, cnt) for cid, cnt in session.execute(stmt)]
#
# 题 4:
#   stmt = (
#       select(User)
#       .where(User.status == "banned")
#       .order_by(User.created_at.desc())
#       .limit(5)
#   )
#   return session.scalars(stmt).all()
#
# 题 5（写法①，推荐——不动懒加载）:
#   stmt = (
#       select(Order)
#       .where(Order.user_id == user_id)
#       .order_by(Order.created_at.desc())
#       .limit(n)
#   )
#   return list(session.scalars(stmt).all())
#
# 题 6:
#   month = func.to_char(func.date_trunc("month", Order.created_at), "YYYY-MM")
#   stmt = (
#       select(month.label("m"), func.count())
#       .where(Order.created_at >= "2026-01-01", Order.created_at < "2027-01-01")
#       .group_by(month)
#       .order_by(month)
#   )
#   return [(m, cnt) for m, cnt in session.execute(stmt)]
#
# 题 7:
#   u = User(email="ex07@test", nickname="ex07", status="active", created_at=datetime.now())
#   session.add(u)
#   session.flush()                     # 拿主键
#   u.nickname = "ex07_v2"
#   session.commit()
#   session.expire(u)                   # 或重开 session 验证
#   assert u.nickname == "ex07_v2"
#   session.delete(u)
#   session.commit()
#   return session.get(User, u.id) is None
#
# 题 8:
#   users = session.scalars(
#       select(User).options(selectinload(User.orders)).order_by(User.id).limit(n_users)
#   ).all()
#   return sum(len(u.orders) for u in users)
