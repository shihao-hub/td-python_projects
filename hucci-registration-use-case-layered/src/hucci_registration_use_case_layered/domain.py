from dataclasses import dataclass


class CourseFullError(Exception):
    """课程已满。"""


class AlreadyEnrolledError(Exception):
    """该学生已报名过这个课程。"""


@dataclass
class Student:
    id: str


@dataclass
class Course:
    id: str
    capacity: int  # 容量
    roster: list[Student]  # 选课的学生

    def enroll(self, student: Student) -> None:
        """建模报名这个行为：学生报名这个课程。"""
        if student.id in {enrolled.id for enrolled in self.roster}:
            raise AlreadyEnrolledError()
        if len(self.roster) >= self.capacity:
            raise CourseFullError()
        self.roster.append(student)