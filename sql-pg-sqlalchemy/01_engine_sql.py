"""练习 1：Engine 与原生 SQL —— 先不碰 ORM，直连数据库写 SQL。

核心概念：
- Engine（引擎）：连接管理器，内置连接池，整个进程通常只建一个；
- text()：把字符串标记为 SQL 语句（支持 :参数 绑定，防 SQL 注入）；
- 事务：engine.begin() 块内所有语句要么全成功要么全回滚（类似 Django 的 atomic）。

Django 对照：Django 里你几乎不直接摸连接（只有 connection.cursor() 才会），
SQLAlchemy 把「连接」显式交到你手上——这是理解它的第一步。
"""
from sqlalchemy import text

from db import engine


def main() -> None:
    # 方式一：engine.connect() —— 需要自己 commit（查询语句不需要）
    with engine.connect() as conn:
        # 团队规范：手写 SQL 一律显式列出字段，禁止 SELECT *
        version = conn.execute(text("SELECT version()")).scalar_one()
        print(f"[1] 服务器版本：{version}")

        rows = (
            conn.execute(text("SELECT id, name, email FROM students ORDER BY id LIMIT 5"))
            .mappings()
            .all()
        )
        print("[2] 前 5 名学生：")
        for row in rows:
            print(f"    {row['id']}  {row['name']:<6} {row['email']}")

    # 方式二：engine.begin() —— 事务块，离开 with 自动 commit，异常自动 rollback
    with engine.begin() as conn:
        cnt = conn.execute(text("SELECT count(student_id) FROM enrollments")).scalar_one()
        print(f"[3] 选课记录数：{cnt}")

    # 参数绑定：:kw 占位符 + 字典传参。绝不许用 f-string 拼 SQL —— SQL 注入！
    with engine.connect() as conn:
        hit = conn.execute(
            text("SELECT id, name FROM students WHERE name LIKE :kw"),
            {"kw": "张%"},
        ).all()
        print(f"[4] 姓张的学生：{hit}")

    # ---------- 练习 ----------
    # 1) 查询所有 2 年级学生的 name、email（WHERE grade = 2）
    # 2) 统计每个年级的人数（GROUP BY grade），显式列出字段
    # 写在下面，模仿 [4] 的参数绑定写法：


if __name__ == "__main__":
    main()
