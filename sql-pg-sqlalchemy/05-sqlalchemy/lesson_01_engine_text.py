# -*- coding: utf-8 -*-
"""
lesson_01：Engine、连接、text() 参数绑定
运行：python lesson_01_engine_text.py
"""
import os

from sqlalchemy import create_engine, text

# 连接串可用环境变量覆盖；echo=True 打印所有 SQL（学习期建议常开）
DB_URL = os.environ.get("LEARN_PG_URL",
                        "postgresql+psycopg2://postgres:postgresql@localhost:5432/learn_pg")
engine = create_engine(DB_URL, echo=True)

# ---------- 1. 第一次连接：SELECT 1 ----------
with engine.connect() as conn:            # with = 借用连接，退出时归还连接池（不是物理关闭）
    result = conn.execute(text("SELECT 1"))
    print("SELECT 1 →", result.scalar())

# ---------- 2. text() + 绑定参数（本项目所有裸 SQL 都这么写） ----------
with engine.connect() as conn:
    # :status / :n 是占位符，真正的值在第二个参数里 —— SQL 结构与数据分离
    result = conn.execute(
        text("""
            SELECT id, email, nickname, status
            FROM users
            WHERE status = :status
            ORDER BY id
            LIMIT :n
        """),
        {"status": "banned", "n": 3},
    )
    print("\nbanned 用户前 3：")
    for row in result:
        print(" ", row.id, row.email, row.nickname, row.status)

    # 常用取值方式
    cnt = conn.execute(text("SELECT count(*) FROM orders")).scalar()                    # 单值
    row = conn.execute(text("SELECT id, total FROM orders WHERE id = :i"), {"i": 1}).one()  # 恰好一行
    mapping = conn.execute(
        text("SELECT status, count(*) AS cnt FROM orders GROUP BY status ORDER BY cnt DESC")
    ).mappings().all()                                                                   # dict 式行
    print("\norders 总数:", cnt)
    print("订单 1:", row.id, row.total)
    print("状态分布第一行:", dict(mapping[0]))

# ---------- 3. 注入现场：为什么禁止字符串拼接 ----------
malicious_input = "x' OR '1'='1"     # 攻击者输入

with engine.connect() as conn:
    # ❌ 拼接：输入直接变成了 SQL 的一部分 —— 条件恒真，全表泄露
    #    （这里只查 count 演示无害，但攻击面已经打开）
    bad_sql = f"SELECT count(*) FROM users WHERE nickname = '{malicious_input}'"
    leaked = conn.execute(text(bad_sql)).scalar()
    print(f"\n拼接 SQL：输入 {malicious_input!r} → 匹配了 {leaked} 行（本应是 0 行！）")

    # ✅ 绑定参数：输入永远是"值"，不可能变成 SQL 结构
    safe = conn.execute(
        text("SELECT count(*) FROM users WHERE nickname = :nick"),
        {"nick": malicious_input},
    ).scalar()
    print(f"绑定参数：同样的输入 → 匹配 {safe} 行（安全）")

# ---------- 4. 事务：读操作之外，写操作必须包事务 ----------
with engine.begin() as conn:          # begin() = connect + 事务 + 成功 commit / 失败 rollback
    conn.execute(text("SELECT 1"))    # 只读演示；真实写入见 lesson_04

# ---------- 5. 连接池状态 ----------
with engine.connect() as conn:
    print("\n连接池状态:", engine.pool.status())
# 连接被"归还"进池，下一个人复用 —— 这就是为什么 Engine 应该是全局单例

engine.dispose()   # 进程退出前释放（演示用；长跑进程不必频繁 dispose）
