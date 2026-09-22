"""最小可运行的报名用例 smoke test。"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
from pathlib import Path

from .domain import AlreadyEnrolledError, CourseFullError
from .db import SqliteDb
from .service import EnrollService


def _seed_database(db_path: Path) -> None:
    """写入 smoke test 所需的基础数据。"""
    with contextlib.closing(sqlite3.connect(db_path)) as connection:
        with connection:
            connection.executemany(
                "INSERT INTO students (id) VALUES (?)",
                [("student-1",), ("student-2",)],
            )
            connection.execute(
                "INSERT INTO courses (id, capacity) VALUES (?, ?)",
                ("course-1", 1),
            )


def main() -> None:
    """验证报名成功、重复报名和课程容量限制。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        db_path = Path(temporary_directory) / "school.db"
        db = SqliteDb(db_path)
        _seed_database(db_path)
        service = EnrollService(db)

        service.enroll("student-1", "course-1")

        try:
            service.enroll("student-1", "course-1")
        except AlreadyEnrolledError:
            pass
        else:
            raise AssertionError("重复报名未被拒绝")

        try:
            service.enroll("student-2", "course-1")
        except CourseFullError:
            pass
        else:
            raise AssertionError("超出课程容量的报名未被拒绝")

        assert [student.id for student in db.get_course("course-1").roster] == [
            "student-1"
        ]


if __name__ == "__main__":
    main()
    print("报名用例 smoke test 通过")
