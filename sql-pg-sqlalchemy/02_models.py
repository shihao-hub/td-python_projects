"""练习 2：模型定义 —— 对照 Django models 学（本练习无需连库，直接可跑）。

先看 models.py 里的注释和字段对照表，再回来跑本文件，观察：
1. SQLAlchemy 生成的建表 DDL 长什么样；
2. 表的元数据（列/索引/约束）如何在 Python 里检查。

关键认知：
- Base.metadata 是「元数据注册表」：模型类定义（被 import）后自动登记；
- create_all() 只建缺的表，不会改已有的表——改表结构要用 Alembic
  （SQLAlchemy 生态的迁移工具，对标 Django 的 migrations，后面工作中再学）。
"""
from sqlalchemy import func
from sqlalchemy.schema import CreateTable

import models
from db import Base
from models import Student


def main() -> None:
    # 1. 看 students 表的建表 DDL（纯本地编译，不连库）
    print("[1] students 表 DDL：")
    print(CreateTable(Student.__table__))

    # 2. 遍历列元数据：名称 / 类型 / 可空 / 主键 / 索引
    print("[2] students 列清单：")
    for col in Student.__table__.columns:
        print(
            f"    {col.name:<14} {str(col.type):<14} "
            f"nullable={col.nullable!s:<5} pk={col.primary_key!s:<5}"
        )

    # 3. 索引与约束（unique=True 的 email 自动带唯一索引）
    print("[3] students 索引：")
    for idx in Student.__table__.indexes:
        print(f"    {idx.name}: {[c.name for c in idx.columns]}")

    # 4. 已注册的所有表（setup_db.py 的 create_all 就是用这份元数据建表的）
    print(f"[4] Base.metadata 里已注册的表：{sorted(Base.metadata.tables)}")

    # ---------- 练习 ----------
    # 1) 给 Student 加一个字段 age: Mapped[int | None] = mapped_column()，
    #    重跑本文件看 DDL 变化（然后删掉——create_all 不会给已有表加列，这正是 Alembic 存在的原因）；
    # 2) 打印 Enrollment 的 DDL，观察复合主键怎么写的。


if __name__ == "__main__":
    main()
