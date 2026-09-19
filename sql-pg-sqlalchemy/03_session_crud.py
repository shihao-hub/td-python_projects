"""练习 3：Session 增删改查 —— ORM 的「工作台」。

和 Django 最大的差异（务必建立直觉）：
- Django：Student.objects.create(...) 立刻发 SQL；
- SQLAlchemy：session.add(obj) 只是登记，commit() 时才统一发 SQL
  ——这叫「工作单元（Unit of Work）」，多次修改会被合并成尽量少的语句。
- identity map：同一个 Session 里，同一行数据只对应一个 Python 对象，
  session.get(Student, 1) 第二次调用不再发 SQL。
"""
from db import SessionLocal
from models import Student


def main() -> None:
    with SessionLocal() as session:
        # ---------- 增：add + commit ----------
        s = Student(name="测试同学", email="temp@test.dev", grade=2, department_id=1)
        session.add(s)
        print(f"[1] add 之后：id = {s.id}（还没发 SQL，主键都还没生成）")
        session.commit()
        print(f"[2] commit 之后：id = {s.id}（INSERT 已发出，主键已回填到对象上）")

        # ---------- 查：get（按主键，走 identity map） ----------
        a = session.get(Student, s.id)
        b = session.get(Student, s.id)
        print(f"[3] identity map：a is b → {a is b}（注意 [3] 前后只发了一次 SQL）")

        # ---------- 改：直接改属性 + commit ----------
        a.name = "测试同学·改"
        session.commit()  # 此时才发 UPDATE
        print(f"[4] 已更新姓名：{a.name}")

        # ---------- 删：delete + commit ----------
        session.delete(a)
        session.commit()
        print("[5] 已删除（DELETE 已发出）")

    # 体会一下：把上面任意一个 commit 注释掉，观察差异——
    # Session 离开 with 时若有未提交的修改会被 rollback（不会自动提交）。

    # ---------- 练习 ----------
    # 1) 新开一个 Session，插入一门课《SQLAlchemy 实战》（4 学分，学院 1）
    # 2) 把《数据结构与算法》的学分改成 4
    # 3) 删掉你刚插的课
    # 提示：写操作都要 commit；盯着控制台，注意 SQL 发出的时机和条数。


if __name__ == "__main__":
    main()
