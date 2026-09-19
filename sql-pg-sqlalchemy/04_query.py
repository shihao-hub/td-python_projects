"""练习 4：select() 查询构建 —— 对照 Django QuerySet 学。

Django → SQLAlchemy 速查：
    filter(grade=2)                      where(Student.grade == 2)
    name__contains="张"                  where(Student.name.like("%张%"))
    name__icontains="z"                  where(Student.name.ilike("%z%"))
    id__in=[1, 2, 3]                     where(Student.id.in_([1, 2, 3]))
    department_id__isnull=True           where(Student.department_id.is_(None))
    exclude(...)                         where(not_(...)) 或取反 ~
    Q(a) | Q(b)                          or_(a, b)
    order_by("-id")                      order_by(Student.id.desc())
    切片 [10:20]                         offset(10).limit(10)
    count() / Avg(...)                   select(func.count()) / func.avg(...)

取结果三件套：
    session.scalars(stmt).all()   → [Student, ...]   实体对象列表（查整张"模型"时用）
    session.execute(stmt).all()   → [Row, ...]       元组行（查若干列/聚合时用）
    session.scalar(stmt)          → 单个值
"""
from sqlalchemy import and_, func, or_, select

from db import SessionLocal
from models import Course, Department, Enrollment, Student


def main() -> None:
    with SessionLocal() as session:
        # 1. 基础过滤 + 排序 + 分页
        stmt = (
            select(Student)
            .where(Student.grade == 2)
            .order_by(Student.id.desc())
            .limit(5)
        )
        print("[1] 2 年级学生（id 倒序前 5）：")
        for s in session.scalars(stmt):
            print(f"    {s.id} {s.name}（{s.email}）")

        # 2. 组合条件：or_ / and_，嵌套即括号分组
        stmt = select(Student.name, Student.email).where(
            or_(
                Student.name.like("张%"),
                and_(Student.grade >= 2, Student.department_id == 1),
            )
        )
        print(f"[2] 组合条件：{session.execute(stmt).all()}")

        # 3. 聚合分组（对照 Django 的 values + annotate）
        stmt = (
            select(Department.name, func.count(Student.id).label("stu_cnt"))
            .join(Student, Student.department_id == Department.id)
            .group_by(Department.name)
            .having(func.count(Student.id) >= 3)
            .order_by(func.count(Student.id).desc())
        )
        print("[3] 各学院人数（≥3 人）：")
        for dept_name, cnt in session.execute(stmt):
            print(f"    {dept_name}: {cnt} 人")

        # 4. 三表关联聚合：每门课的平均分
        stmt = (
            select(Course.title, func.round(func.avg(Enrollment.score), 1))
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(Enrollment.score.is_not(None))
            .group_by(Course.title)
            .order_by(func.avg(Enrollment.score).desc())
        )
        print("[4] 各课程平均分（高→低）：")
        for title, avg in session.execute(stmt):
            print(f"    {title}: {avg}")

    # ---------- 练习：把下面的 Django ORM 翻译成 SQLAlchemy ----------
    # 1) Student.objects.filter(grade=3).order_by("name")[:3]
    # 2) Student.objects.filter(Q(name__startswith="李") | Q(email__endswith="@qq.com"))
    # 3) 每个年级人数，只要人数 > 2 的，按人数倒序
    #
    # ---------- 参考答案 ----------
    # 1) select(Student).where(Student.grade == 3).order_by(Student.name).limit(3)
    # 2) select(Student).where(
    #        or_(Student.name.like("李%"), Student.email.like("%@qq.com")))
    # 3) select(Student.grade, func.count(Student.id))
    #        .group_by(Student.grade)
    #        .having(func.count(Student.id) > 2)
    #        .order_by(func.count(Student.id).desc())


if __name__ == "__main__":
    main()
