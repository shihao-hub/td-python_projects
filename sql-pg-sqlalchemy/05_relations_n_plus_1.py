"""练习 5：关联查询与加载策略 —— 直击 N+1（慢查询最常见的根因之一）。

先记住默认行为：relationship 默认 lazy="select"——
第一次访问 dept.students 时才发一条 SQL。
在循环里访问 = 每轮循环一条 SQL = 经典 N+1（1 次查主表 + N 次查关联）。

Django 对照（几乎一一对应）：
    selectinload(Department.students)   ↔  prefetch_related（IN 一把捞）
    joinedload(Department.students)     ↔  select_related（JOIN 一次捞）
"""
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from db import SessionLocal
from models import Course, Department, Student


def main() -> None:
    with SessionLocal() as session:
        # ---------- 1. join + 跨表条件 ----------
        stmt = (
            select(Student.name, Department.name)
            .join(Department, Student.department_id == Department.id)
            .where(Department.name == "计算机学院")
        )
        print(f"[1] 计算机学院学生：{[r[0] for r in session.execute(stmt)]}")

        # ---------- 2. N+1 现场：数一数控制台发了几条 SELECT ----------
        print("[2] N+1 演示（默认 lazy）——注意 SELECT 数量：")
        for dept in session.scalars(select(Department)):
            n = len(dept.students)  # 每次访问都发一条 SELECT！
            print(f"     {dept.name}: {n} 人")

        # ---------- 3. 修复：selectinload（= prefetch_related） ----------
        print("[3] selectinload 修复——一条 SQL 搞定所有学院的学生：")
        stmt = select(Department).options(selectinload(Department.students))
        for dept in session.scalars(stmt):
            print(f"     {dept.name}: {len(dept.students)} 人")

        # ---------- 4. joinedload（= select_related）+ 按关联属性写 join ----------
        stmt = (
            select(Course)
            .join(Course.department)  # 直接用 relationship 定 join，不用手写 on 条件
            .where(Department.name == "计算机学院")
            .options(joinedload(Course.department))
        )
        print(f"[4] 计算机学院的课程：{session.scalars(stmt).all()}")

    # ---------- 练习 ----------
    # 1) 用 selectinload 一次取出所有学生及其选课记录（Student.enrollments）
    # 2) join 写法查「数学科学学院」所有学生姓名
    # 3) 想一想：教务系统首页「每个学院第一门课」，怎么写不产生 N+1？


if __name__ == "__main__":
    main()
