from __future__ import annotations

import contextlib
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from .domain import Course, Student


class Database(ABC):
    @abstractmethod
    def get_student(self, id: str) -> Student:
        """返回学生。"""

    @abstractmethod
    def get_course(self, id: str) -> Course:
        """返回课程。"""

    @abstractmethod
    def save_course(self, course: Course) -> None:
        """保存课程。"""


class SqliteDb(Database):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with contextlib.closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS students (
                        id TEXT PRIMARY KEY
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS courses (
                        id TEXT PRIMARY KEY,
                        capacity INTEGER NOT NULL CHECK (capacity >= 0)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS enrollments (
                        student_id TEXT NOT NULL,
                        course_id TEXT NOT NULL,
                        PRIMARY KEY (student_id, course_id),
                        FOREIGN KEY (student_id) REFERENCES students(id),
                        FOREIGN KEY (course_id) REFERENCES courses(id)
                    )
                    """
                )

    def get_student(self, id: str) -> Student:
        with contextlib.closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT id FROM students WHERE id = ?",
                (id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"学生不存在: {id}")
        return Student(id=row["id"])

    def get_course(self, id: str) -> Course:
        with contextlib.closing(self._connect()) as connection:
            course_row = connection.execute(
                "SELECT id, capacity FROM courses WHERE id = ?",
                (id,),
            ).fetchone()
            if course_row is None:
                raise LookupError(f"课程不存在: {id}")

            student_rows = connection.execute(
                """
                SELECT students.id
                FROM students
                JOIN enrollments ON enrollments.student_id = students.id
                WHERE enrollments.course_id = ?
                ORDER BY students.id
                """,
                (id,),
            ).fetchall()

        return Course(
            id=course_row["id"],
            capacity=course_row["capacity"],
            roster=[Student(id=row["id"]) for row in student_rows],
        )

    def save_course(self, course: Course) -> None:
        with contextlib.closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM enrollments WHERE course_id = ?",
                    (course.id,),
                )
                connection.executemany(
                    "INSERT INTO enrollments (student_id, course_id) VALUES (?, ?)",
                    [(student.id, course.id) for student in course.roster],
                )
