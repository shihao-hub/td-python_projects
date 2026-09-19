"""一键初始化：建库 → 建表 → 灌测试数据（可反复运行，每次重置）。

运行前：复制 .env.example 为 .env，填上本机 PostgreSQL 的密码。
运行：uv run python setup_db.py
"""
from decimal import Decimal

from sqlalchemy import create_engine, func, insert, select, text

import models  # noqa: F401  导入即把所有模型注册进 Base.metadata
from db import Base, PG_DBNAME, PG_PASSWORD, SessionLocal, build_url, engine

DEPARTMENTS = ["计算机学院", "外国语学院", "数学科学学院"]

COURSES = [
    # (课程名, 学分, 学院序号 1~3)
    ("Python 程序设计", 4, 1),
    ("数据结构与算法", 5, 1),
    ("数据库系统", 4, 1),
    ("英语精读", 3, 2),
    ("翻译理论与实践", 3, 2),
    ("数学分析", 5, 3),
]

STUDENTS = [
    # (姓名, 邮箱, 年级, 学院序号 1~3)
    ("张伟", "zhangwei@qq.com", 2, 1),
    ("张倩", "zhangqian@qq.com", 1, 1),
    ("李娜", "lina@qq.com", 3, 2),
    ("李强", "liqiang@163.com", 2, 1),
    ("王芳", "wangfang@qq.com", 1, 2),
    ("王磊", "wanglei@gmail.com", 3, 1),
    ("刘洋", "liuyang@qq.com", 2, 3),
    ("陈静", "chenjing@qq.com", 1, 3),
    ("杨帆", "yangfan@163.com", 2, 2),
    ("赵敏", "zhaomin@qq.com", 3, 3),
    ("孙浩", "sunhao@qq.com", 1, 1),
    ("周婷", "zhouting@qq.com", 2, 2),
    ("吴宇欣", "wuyuxin@qq.com", 1, 3),
    ("郑凯", "zhengkai@163.com", 3, 1),
    ("林小雨", "linxiaoyu@qq.com", 2, 3),
    ("黄一鸣", "huangyiming@qq.com", 1, 2),
]

ENROLLMENTS = [
    # (学生序号 1~16, 课程序号 1~6, 分数或 None 未出分)
    (1, 1, 92), (1, 2, 78), (1, 3, 85),
    (2, 1, 66), (2, 3, None),
    (3, 4, 90), (3, 5, 88),
    (4, 1, 73), (4, 2, 61), (4, 3, 70),
    (5, 4, 82),
    (6, 1, 95), (6, 2, 89), (6, 3, 91),
    (7, 6, 77),
    (8, 6, 84), (8, 1, None),
    (9, 4, 71), (9, 5, 79),
    (10, 6, 68),
    (11, 1, 58), (11, 2, None),
    (12, 4, 93), (12, 5, 86),
    (13, 6, 81),
    (14, 1, 64), (14, 3, 72),
    (15, 6, 87),
    (16, 4, 76),
]


def ensure_database() -> None:
    """连默认的 postgres 库，目标库不存在则创建。

    CREATE DATABASE 不能在事务里执行，所以要 AUTOCOMMIT 隔离级别。
    """
    if not PG_PASSWORD:
        raise SystemExit("缺少 PG_PASSWORD：请先复制 .env.example 为 .env 并填写密码。")
    admin = create_engine(build_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": PG_DBNAME},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{PG_DBNAME}"'))
            print(f"[setup] 已创建数据库 {PG_DBNAME}")
        else:
            print(f"[setup] 数据库 {PG_DBNAME} 已存在，跳过建库")
    admin.dispose()


def create_tables() -> None:
    """删旧表再重建，保证每次练习数据一致（实验室项目可以这么玩，生产禁用！）。"""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print(f"[setup] 已在 {PG_DBNAME} 中重建全部表：{sorted(Base.metadata.tables)}")


def seed() -> None:
    with SessionLocal() as session:
        session.execute(insert(models.Department), [{"name": n} for n in DEPARTMENTS])
        session.execute(
            insert(models.Course),
            [{"title": t, "credits": c, "department_id": d} for t, c, d in COURSES],
        )
        session.execute(
            insert(models.Student),
            [
                {"name": n, "email": e, "grade": g, "department_id": d}
                for n, e, g, d in STUDENTS
            ],
        )
        session.execute(
            insert(models.Enrollment),
            [
                {"student_id": s, "course_id": c, "score": Decimal(str(sc)) if sc is not None else None}
                for s, c, sc in ENROLLMENTS
            ],
        )
        session.commit()

        dept_cnt = session.scalar(select(func.count()).select_from(models.Department))
        stu_cnt = session.scalar(select(func.count()).select_from(models.Student))
        enr_cnt = session.scalar(select(func.count()).select_from(models.Enrollment))
    print(f"[setup] 已灌入数据：{dept_cnt} 学院 / {stu_cnt} 学生 / {enr_cnt} 选课记录")
    print("[setup] 完成！按 01 → 07 顺序运行练习文件即可。")


if __name__ == "__main__":
    ensure_database()
    create_tables()
    seed()
