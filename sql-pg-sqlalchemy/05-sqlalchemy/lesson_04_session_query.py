# -*- coding: utf-8 -*-
"""
lesson_04：Session 生命周期 + 用 ORM 重写阶段 1/2 的经典查询
运行：python lesson_04_session_query.py
重点：Session ≠ QuerySet —— 事务范围 + Identity Map + dirty 跟踪。
"""
import os
from datetime import datetime

from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session

from lesson_03_orm_model import Base, User, Order, engine   # 复用上一课定义的模型

# ---------- 1. Session 是事务范围 + 对象缓存 ----------
with Session(engine) as session:
    # Identity Map：同一主键在一个 Session 里只有一个 Python 对象
    a = session.get(User, 42)
    b = session.get(User, 42)
    print("identity map（同一对象，第二次不发 SQL）:", a is b)      # True

    # 查询结果也会进缓存：再 select 同一个人，拿到的还是同一个对象
    c = session.scalars(select(User).where(User.id == 42)).one()
    print("select 再查也是同一对象:", a is c)                        # True

    # 修改 = 改对象属性；Session 自动跟踪 dirty，commit 时统一 UPDATE
    a.nickname = "nick_42_edited"
    print("commit 前 dirty 对象:", session.dirty)                    # identity set 里有它
    # （不 commit 就退出 with —— 事务回滚，改动丢弃。接下来故意回滚）

# 验证改动没有落库（上面没 commit）
with Session(engine) as session:
    print("回滚后昵称:", session.get(User, 42).nickname)             # nick_42（未变）

# ---------- 2. 写入三步：add → commit → （需要时）refresh ----------
with Session(engine) as session:
    test_user = User(email="lesson04@example.com", nickname="lesson04_test",
                     status="active", created_at=datetime.now())
    session.add(test_user)
    session.flush()                          # flush：立即发 SQL（拿主键），但仍未提交
    print("flush 后有主键了:", test_user.id)

    test_user.nickname = "lesson04_test_v2"  # commit 前继续改，只会生成一条最终 UPDATE
    session.commit()

    session.delete(test_user)                # 删除也要 commit
    session.commit()
    print("已删除测试用户")

# ---------- 3. rollback：把事务还原 ----------
with Session(engine) as session:
    u = session.get(User, 1)
    old = u.nickname
    u.nickname = "hacked"
    session.rollback()                       # 回滚：对象状态恢复
    print("rollback 后:", u.nickname == old)

# ---------- 4. 重写阶段 1/2 的经典查询（ORM 顺手就能写） ----------
with Session(engine) as session:
    # 阶段 1 题 5：按状态统计订单数与金额
    stmt = (
        select(Order.status, func.count().label("cnt"), func.sum(Order.total).label("amount"))
        .group_by(Order.status)
        .order_by(func.count().desc())
    )
    print("\n[阶段1-题5] 状态统计:")
    for status, cnt, amount in session.execute(stmt):
        print(f"   {status:10} {cnt:>7} {amount:>16,.2f}")

    # 阶段 2 题 7：每种状态金额最大的 3 笔（窗口函数要包子查询，不能直接进 WHERE）
    rn = func.row_number().over(partition_by=Order.status, order_by=Order.total.desc()).label("rn")
    subq = select(Order.status, Order.id, Order.total, rn).subquery()
    stmt = select(subq.c.status, subq.c.id, subq.c.total).where(subq.c.rn <= 3).order_by(subq.c.status)
    print("\n[阶段2-题7] 每状态 Top3 金额:")
    for status, oid, total in session.execute(stmt):
        print(f"   {status:10} #{oid:<7} {total:>10}")

    # 阶段 1 题 9：没有 pending 订单的用户（NOT EXISTS）
    from sqlalchemy import exists
    stmt = select(func.count()).select_from(User).where(
        ~exists().where(Order.user_id == User.id, Order.status == "pending")
    )
    print("\n[阶段1-题9] 没有 pending 订单的用户数:", session.scalar(stmt))

# ---------- 5. Session 的纪律 ----------
print("""
Session 使用纪律（Django 经验迁移注意事项）：
 1. 一个工作单元一个 Session：with Session(engine) as s: ...（自动 close）
 2. 改动靠 commit 落库：忘了 commit = 改动消失（QuerySet 的 .update() 是立即的，习惯要改）
 3. commit 后对象属性默认过期（expire_on_commit），下次访问重新 SELECT
 4. Session 关闭后访问属性 → DetachedInstanceError（lesson_05 会碰到）
 5. 不要把 Session 存到全局/长生命周期对象里
""")
