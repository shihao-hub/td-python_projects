# -*- coding: utf-8 -*-
"""
lesson_03：ORM 模型 —— DeclarativeBase / mapped_column / relationship
运行：python lesson_03_orm_model.py
重点：模型映射到【已存在】的 learn_pg 表（不建表）；类型注解即列定义。
说明：本文件同时被 lesson_04/05 和 exercises.py 导入（复用模型定义），
     所以演示代码放在 main() 里，只有直接运行才会执行。
"""
import os
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import ForeignKey, create_engine, select, func
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, Session,
)

DB_URL = os.environ.get("LEARN_PG_URL",
                        "postgresql+psycopg2://postgres:postgresql@localhost:5432/learn_pg")
engine = create_engine(DB_URL, echo=False)


# ---------- 1. 定义模型（映射已有表，不执行 create_all） ----------
class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str]                                  # 非空约束由映射类型推断（Optional → nullable）
    nickname: Mapped[str]
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime]

    # 一对多：一个用户多张订单。ORM 惯例：双向关系用 back_populates 手动配对
    orders: Mapped[list["Order"]] = relationship(back_populates="user")

    def __repr__(self):
        return f"<User id={self.id} {self.nickname}>"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str]
    total: Mapped[Decimal]
    created_at: Mapped[datetime]
    paid_at: Mapped[Optional[datetime]]                 # Optional = 可空列

    user: Mapped["User"] = relationship(back_populates="orders")

    def __repr__(self):
        return f"<Order id={self.id} user={self.user_id} {self.status} {self.total}>"


# ---------- 2. ORM 查询：2.0 统一 API（session.execute / scalars） ----------
def main() -> None:
    with Session(engine) as session:
        # 2.0 风格：select() + session.scalars()（直接拿对象而不是行元组）
        banned = session.scalars(
            select(User).where(User.status == "banned").order_by(User.id).limit(3)
        ).all()
        print("banned 用户:", banned)

        # 主键查询（≈ Django 的 User.objects.get(pk=42)，但找不到返回 None 而不是抛异常）
        u = session.get(User, 42)
        print("主键查询:", u, "订单数(访问 .orders 触发 1 条惰性 SQL):", len(u.orders))

        # ORM 实体 + Core 函数混搭
        stmt = (
            select(User.nickname, func.count(Order.id).label("cnt"))
            .join(Order, Order.user_id == User.id)
            .group_by(User.id, User.nickname)
            .order_by(func.count(Order.id).desc())
            .limit(5)
        )
        print("\n下单最多的 5 个用户：")
        for nickname, cnt in session.execute(stmt):
            print(f"   {nickname}: {cnt}")

        # 窗口函数（阶段 2 题 7 的 ORM 版）：每种状态金额最大的 3 笔
        # 注意阶段 2 的教训：窗口函数不能直接进 WHERE → 先包成子查询再筛
        rn = func.row_number().over(
            partition_by=Order.status, order_by=Order.total.desc()
        ).label("rn")
        subq = select(Order.id, Order.status, Order.total, rn).subquery()
        stmt = (
            select(subq.c.id, subq.c.status, subq.c.total)
            .where(subq.c.rn <= 3)
            .order_by(subq.c.status, subq.c.total.desc())
        )
        print("\n每种状态最大 3 笔订单：")
        for order in session.execute(stmt):
            print(f"   {order.status:10} #{order.id} {order.total}")

    print("\n小结：")
    print(" * Mapped[类型] 即列定义；Optional[...] = 可空")
    print(" * relationship 不碰数据库结构，只是 ORM 层的'导航属性'（≈ Django 的反向关系）")
    print(" * 查询永远是 select(...)，返回实体用 scalars()，返回列组合用 execute()")
    print(" * 下一课讲 Session 的生命周期 —— Django Session 惯性最容易翻车的地方")


if __name__ == "__main__":
    main()
