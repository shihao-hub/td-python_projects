from __future__ import annotations

from .db import Database


class EnrollService:
    """编排报名流程。"""

    def __init__(self, db: Database):
        self.db = db

    def enroll(self, student_id: str, course_id: str) -> None:
        """读取学生和课程，执行业务校验后保存课程。"""
        student = self.db.get_student(student_id)
        course = self.db.get_course(course_id)
        course.enroll(student)
        self.db.save_course(course)


# 保留旧拼写，避免现有示例代码导入失败。
ErollService = EnrollService
