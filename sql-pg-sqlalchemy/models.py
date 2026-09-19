"""ORM 模型定义 —— 对照 Django models.py 阅读。

字段类型对照速记：
    Mapped[int]              ↔ IntegerField          String(50)        ↔ CharField(max_length=50)
    Mapped[str]              ↔ TextField/CharField   Numeric(5, 2)     ↔ DecimalField
    Mapped[datetime]         ↔ DateTimeField         ForeignKey(...)   ↔ ForeignKey(to)
    Mapped[X | None]         ↔ null=True             primary_key=True  ↔ primary_key=True

关键差异：
- Django 的 related_name 在 SQLAlchemy 里是一对 back_populates（两边互相指名）；
- Django 的 ManyToManyField 通过 through= 建中间表，SQLAlchemy 直接把中间表
  建成普通模型（见 Enrollment），多对多关系更透明。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class Department(Base):
    """学院（一对多的「一」端）。"""
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    # 反向关系：这个学院的所有学生/课程（Django: student_set 或 related_name 指定的名字）
    students: Mapped[list["Student"]] = relationship(back_populates="department")
    courses: Mapped[list["Course"]] = relationship(back_populates="department")

    def __repr__(self) -> str:
        return f"<Department {self.id} {self.name}>"


class Course(Base):
    """课程。"""
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100), index=True)  # 单列索引：index=True
    credits: Mapped[int] = mapped_column(default=3)  # Python 端默认值

    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    department: Mapped["Department"] = relationship(back_populates="courses")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="course")

    def __repr__(self) -> str:
        return f"<Course {self.id} {self.title}>"


class Student(Base):
    """学生。"""
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), index=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)  # 唯一约束自带索引
    grade: Mapped[int] = mapped_column(default=1)
    # server_default：默认值由数据库端生成（思想类似 Django 的 auto_now_add）
    enrolled_at: Mapped[datetime] = mapped_column(server_default=func.now())

    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    department: Mapped["Department | None"] = relationship(back_populates="students")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="student")

    # 组合索引 (department_id, grade)：高频查询「某学院某年级」用得上。
    # 呼应工作中的索引设计：组合索引遵循最左前缀原则——
    # 查 department_id 走它，查 (department_id, grade) 走它，只查 grade 不走它
    __table_args__ = (Index("ix_students_dept_grade", "department_id", "grade"),)

    def __repr__(self) -> str:
        return f"<Student {self.id} {self.name}>"


class Enrollment(Base):
    """选课记录 = 学生↔课程的多对多中间表（Django: ManyToManyField through=Enrollment）。

    复合主键 (student_id, course_id)：同一门课不能重复选。
    """
    __tablename__ = "enrollments"

    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), primary_key=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # 百分制，未出分为 NULL

    student: Mapped["Student"] = relationship(back_populates="enrollments")
    course: Mapped["Course"] = relationship(back_populates="enrollments")

    def __repr__(self) -> str:
        return f"<Enrollment student={self.student_id} course={self.course_id} score={self.score}>"
