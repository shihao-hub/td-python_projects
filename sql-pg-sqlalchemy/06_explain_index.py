"""练习 6：EXPLAIN ANALYZE 与索引 —— 解决慢查询的基本功，用数据说话。

工作套路：拿到慢 SQL → EXPLAIN ANALYZE 看执行计划 → 判断走没走索引 →
加索引/改写法 → 再 EXPLAIN 对比。

读执行计划抓三点：
1. Seq Scan（全表扫描）还是 Index Scan？大表上的 Seq Scan 通常就是问题；
2. 估算 rows 与 actual rows 差多少？差太远 = 统计信息过期（跑一次 ANALYZE）；
3. 耗时集中在哪个节点？

本练习会自建 perf_users 表并灌 5 万行，直观对比索引前后效果。
"""
from sqlalchemy import create_engine, insert, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base, PG_DBNAME, build_url


class PerfUser(Base):
    """性能实验专用表（仅本练习使用，随便造）。"""
    __tablename__ = "perf_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column()


def explain(engine, sql: str) -> float:
    """跑 EXPLAIN ANALYZE，打印执行计划，返回执行耗时（ms）。"""
    with engine.connect() as conn:
        plan = conn.execute(text("EXPLAIN (ANALYZE, TIMING OFF) " + sql)).scalars().all()
    ms = 0.0
    for line in plan:
        print("    " + line)
        if line.startswith("Execution Time"):
            ms = float(line.split(":")[1].replace("ms", "").strip())
    return ms


def main() -> None:
    # echo=False：灌数据时别打印 SQL，不然刷屏
    engine = create_engine(build_url(PG_DBNAME), echo=False)

    PerfUser.__table__.drop(engine, checkfirst=True)
    PerfUser.__table__.create(engine)
    rows = [{"name": f"用户{i:06d}", "email": f"u{i:06d}@demo.dev"} for i in range(1, 50_001)]
    with engine.begin() as conn:
        for start in range(0, len(rows), 5_000):  # 分批 executemany，官方推荐的批量插入姿势
            conn.execute(insert(PerfUser), rows[start : start + 5_000])
    print("[0] 已灌入 50000 行实验数据\n")

    # 注意两条 SQL 都显式列出字段（规范）
    suffix_sql = "SELECT id, name, email FROM perf_users WHERE email LIKE '%900@%'"
    prefix_sql = "SELECT id, name, email FROM perf_users WHERE email LIKE 'u049900@%'"

    print("[1] 无索引：后缀 LIKE（%xx 无法走 B-tree 索引）")
    t1 = explain(engine, suffix_sql)
    print(f"    → {t1} ms\n")

    print("[2] 无索引：前缀 LIKE")
    t2 = explain(engine, prefix_sql)
    print(f"    → {t2} ms\n")

    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX ix_perf_users_email ON perf_users (email)"))
        conn.execute(text("ANALYZE perf_users"))  # 更新统计信息，让优化器认识新数据分布
    print("[3] 已创建 email 索引并 ANALYZE\n")

    print("[4] 有索引：后缀 LIKE（依旧 Seq Scan——这就是典型「索引失效」）")
    t3 = explain(engine, suffix_sql)
    print(f"    → {t3} ms\n")

    print("[5] 有索引：前缀 LIKE（应走 Index Scan）")
    t4 = explain(engine, prefix_sql)
    print(f"    → {t4} ms\n")

    print("=" * 50)
    print(f"结论：后缀匹配 {t1:.1f} → {t3:.1f} ms（B-tree 救不了 % 开头）")
    print(f"      前缀匹配 {t2:.1f} → {t4:.1f} ms（索引生效，数量级下降）")
    print("延伸：后缀匹配想要索引得用反转列或 pg_trgm GIN 索引，遇到再查。")

    engine.dispose()


if __name__ == "__main__":
    main()
