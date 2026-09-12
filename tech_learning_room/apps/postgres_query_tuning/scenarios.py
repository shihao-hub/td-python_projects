"""五个场景：扫描方式 / 联合索引列序 / 深分页 / N+1 / 部分索引。"""

from __future__ import annotations

from apps.common.lab import Timer, banner, check, conclude, fact, step
from apps.postgres_query_tuning.db import SCHEMA, connect, explain, reset_and_seed, _show


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：全表扫描 vs 索引扫描
# ──────────────────────────────────────────────────────────────────────────

async def scenario_seq_vs_index() -> None:
    banner("场景 1：WHERE user_id = ? —— 无索引的 Seq Scan vs 索引的 Index Scan")
    conn = await connect()
    try:
        sql = f"SELECT id, status, amount FROM {SCHEMA}.orders WHERE user_id = $1 LIMIT 20"
        _show("无索引（全表扫描 10 万行）：", await explain(conn, sql, 4242))
        await conn.execute(f"CREATE INDEX idx_orders_user ON {SCHEMA}.orders (user_id)")
        _show("建立 (user_id) 索引后：", await explain(conn, sql, 4242))
        rows = await conn.fetch(sql, 4242)
        fact("命中行数", len(rows))
        conclude(
            "索引把'读 10 万行过滤'变成'B 树定位 + 读少量行'；"
            "Buffers 里的 shared hit/read 是真实 IO 代价，比 Execution Time 更稳定（不受缓存冷热干扰）。"
            "注意：LIMIT 不能拯救低选择性条件——status='done' 占 2/3，走索引反而更慢，优化器会自动选 Seq Scan。"
        )
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：联合索引的列顺序 —— 等值列在前，范围/排序列在后
# ──────────────────────────────────────────────────────────────────────────

async def scenario_composite_index() -> None:
    banner("场景 2：WHERE status=? AND created_at < ? ORDER BY created_at DESC —— 列顺序的艺术")
    conn = await connect()
    try:
        sql = (
            f"SELECT id, user_id, amount, created_at FROM {SCHEMA}.orders "
            f"WHERE status = $1 AND created_at < now() ORDER BY created_at DESC LIMIT 20"
        )
        _show("无索引：", await explain(conn, sql, "pending"))

        await conn.execute(f"CREATE INDEX idx_bad ON {SCHEMA}.orders (created_at, status)")
        _show("错误顺序 (created_at, status)：", await explain(conn, sql, "pending"))

        await conn.execute(f"CREATE INDEX idx_good ON {SCHEMA}.orders (status, created_at)")
        _show("正确顺序 (status, created_at)：", await explain(conn, sql, "pending"))

        await conn.execute(f"DROP INDEX {SCHEMA}.idx_bad, {SCHEMA}.idx_good")
        conclude(
            "联合索引遵循最左前缀：等值条件列（status）放前面，B 树能一步定位到该前缀区间；"
            "范围/排序列（created_at）放后面，区间扫描天然有序，ORDER BY 可免 Sort 节点。"
            "反过来的 (created_at, status) 对 status 等值毫无帮助，退化成范围扫全时间轴。"
        )
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：OFFSET 深分页 vs 游标分页（keyset）
# ──────────────────────────────────────────────────────────────────────────

async def scenario_offset_vs_keyset() -> None:
    banner("场景 3：翻到第 5000 页 —— OFFSET 99980 的代价 vs 游标分页")
    conn = await connect()
    try:
        await conn.execute(
            f"CREATE INDEX idx_page ON {SCHEMA}.orders (created_at DESC, id DESC)"
        )
        offset_sql = (
            f"SELECT id, amount FROM {SCHEMA}.orders "
            f"ORDER BY created_at DESC, id DESC OFFSET 99980 LIMIT 20"
        )
        _show("OFFSET 99980：", await explain(conn, offset_sql))

        # 取游标：上一页最后一行的 (created_at, id)
        cursor = await conn.fetchrow(
            f"SELECT created_at, id FROM {SCHEMA}.orders ORDER BY created_at DESC, id DESC OFFSET 99979 LIMIT 1"
        )
        keyset_sql = (
            f"SELECT id, amount FROM {SCHEMA}.orders "
            f"WHERE (created_at, id) < ($1, $2) ORDER BY created_at DESC, id DESC LIMIT 20"
        )
        _show(f"游标分页 (created_at,id) < (t_{cursor['id']}, {cursor['id']})：",
              await explain(conn, keyset_sql, cursor["created_at"], cursor["id"]))

        with Timer() as t_off:
            for _ in range(50):
                await conn.fetch(offset_sql)
        with Timer() as t_key:
            for _ in range(50):
                await conn.fetch(keyset_sql, cursor["created_at"], cursor["id"])
        fact("50 次平均耗时", f"OFFSET {t_off.ms/50:.2f}ms vs 游标 {t_key.ms/50:.2f}ms")
        check(t_key.ms < t_off.ms, "游标分页常数级耗时，与页深无关", "本机差异过小，看 EXPLAIN 的行数即可")
        conclude(
            "OFFSET 必须先产出并丢弃前 N 行——页越深越慢，且数据插入会导致翻页错行；"
            "游标分页用 (排序键, 唯一键) 的行比较直接定位起点，代价恒定、结果稳定。"
            "代价是不能随机跳页，只适合信息流/列表下拉这类顺序场景。"
        )
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：N+1 查询 —— 循环单查 vs 一次聚合
# ──────────────────────────────────────────────────────────────────────────

async def scenario_n_plus_one() -> None:
    banner("场景 4：100 个用户的订单数 —— N+1 循环 vs GROUP BY 聚合")
    conn = await connect()
    try:
        users = [r["id"] for r in await conn.fetch(
            f"SELECT id FROM {SCHEMA}.users ORDER BY id LIMIT 100"
        )]

        with Timer() as t_n:
            counts_loop = []
            for uid in users:
                # 每个用户一次往返：ORM 里 lazy-load 的 default 形态
                counts_loop.append(await conn.fetchval(
                    f"SELECT COUNT(*) FROM {SCHEMA}.orders WHERE user_id = $1", uid
                ))
        fact(f"循环查询（{len(users)} 次往返）", f"{t_n.ms:.1f}ms")

        with Timer() as t_one:
            rows = await conn.fetch(
                f"SELECT user_id, COUNT(*) AS cnt FROM {SCHEMA}.orders "
                f"WHERE user_id = ANY($1) GROUP BY user_id", users
            )
            counts_map = {r["user_id"]: r["cnt"] for r in rows}
        fact("单条聚合（1 次往返）", f"{t_one.ms:.1f}ms")

        same = all(counts_map.get(uid, 0) == c for uid, c in zip(users, counts_loop))
        fact("结果一致", same)
        fact("放大倍数", f"{t_n.ms / max(t_one.ms, 0.001):.0f}x（本地 RTT≈0.1ms；"
                        "生产内网 RTT 0.5~1ms 时 100 次 = 50~100ms 纯网络等待）")
        check(same and t_n.ms > t_one.ms, "N+1 的代价随 N 线性放大，且主要耗在网络往返而非 SQL 本身", "未复现")
        conclude(
            "N+1 的识别信号：循环里出现 await session.refresh / 访问未加载关系 / fetch 单条。"
            "修法：selectinload（IN 批量）、joinedload（JOIN）、或手写 GROUP BY。"
            "本地测试感觉不到，是因为 RTT 接近 0——量产后跨网络立刻现形。"
        )
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────
# 场景 5：部分索引 —— 只为 5% 的行建索引
# ──────────────────────────────────────────────────────────────────────────

async def scenario_partial_index() -> None:
    banner("场景 5：只查 error 日志（0.25% of 20 万）—— 普通索引 vs 部分索引")
    conn = await connect()
    try:
        sql = (
            f"SELECT id, created_at FROM {SCHEMA}.event_logs "
            f"WHERE level = 'error' ORDER BY created_at DESC LIMIT 20"
        )
        _show("无索引：", await explain(conn, sql))

        await conn.execute(f"CREATE INDEX idx_logs_level_created ON {SCHEMA}.event_logs (level, created_at)")
        _show("普通联合索引 (level, created_at)：", await explain(conn, sql))

        size_full = await conn.fetchval(
            "SELECT pg_size_pretty(pg_relation_size($1))", f"{SCHEMA}.idx_logs_level_created"
        )
        await conn.execute(f"DROP INDEX {SCHEMA}.idx_logs_level_created")

        await conn.execute(
            f"CREATE INDEX idx_logs_partial ON {SCHEMA}.event_logs (created_at DESC) "
            f"WHERE level = 'error'"
        )
        _show("部分索引 (created_at DESC) WHERE level='error'：", await explain(conn, sql))
        size_partial = await conn.fetchval(
            "SELECT pg_size_pretty(pg_relation_size($1))", f"{SCHEMA}.idx_logs_partial"
        )
        fact("索引大小", f"普通 {size_full} vs 部分 {size_partial}")
        conclude(
            "部分索引只为满足谓词的少量行建 B 树：更小、写入维护更便宜、扫描更少行。"
            "适合'状态机查询'（pending/failed 的工单、未读消息）——"
            "终态数据占绝大多数而非终态极少时，收益最大。"
        )
    finally:
        await conn.close()


SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("seq-vs-index", scenario_seq_vs_index, "全表扫描 vs 索引扫描（Buffers 对照）")
_reg("composite-index", scenario_composite_index, "联合索引列顺序：等值在前、范围在后")
_reg("offset-vs-keyset", scenario_offset_vs_keyset, "深分页：OFFSET 丢弃 N 行 vs 游标定位")
_reg("n-plus-one", scenario_n_plus_one, "N+1 查询：循环单查 vs GROUP BY 聚合")
_reg("partial-index", scenario_partial_index, "部分索引：只为 0.25% 的行建索引")


async def run_all() -> None:
    for fn in SCENARIOS.values():
        await fn()
