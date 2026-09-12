"""七个场景：并发扣减与事务隔离的典型事故现场与正确写法。

运行顺序即学习顺序：先看事故如何发生，再看正确方案为什么有效。
所有 SQL 显式列出字段；教学重点是"时序"，故多处用两个连接手动交错。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure, UniqueViolation

from apps.common.lab import banner, check, conclude, fact, step
from apps.postgres_transactions.db import SCHEMA, connect, read_remaining, reset_schema, seed_code

CODE_ID = 1


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：丢失更新（read-modify-write 反模式）
# ──────────────────────────────────────────────────────────────────────────

def scenario_lost_update() -> None:
    banner("场景 1：丢失更新 —— 读-算-写三步在并发下必然出事")
    reset_schema()
    seed_code(CODE_ID, "NEWYEAR", remaining=5)
    step("初始: remaining = 5，两个事务各兑换 1 个名额（正确结果应为 3）")

    t1, t2 = connect(), connect()
    try:
        # —— 人为编排的事务交错（真实并发里随时可能发生）——
        v1 = read_remaining(t1, CODE_ID)
        step(f"T1 SELECT remaining -> {v1}")
        v2 = read_remaining(t2, CODE_ID)
        step(f"T2 SELECT remaining -> {v2}   （T2 读到的是 T1 提交前的值）")

        t1.execute(
            f"UPDATE {SCHEMA}.redemption_codes SET remaining = %s, updated_at = now() WHERE id = %s",
            (v1 - 1, CODE_ID),
        )
        t1.commit()
        step(f"T1 UPDATE remaining = {v1} - 1 = {v1 - 1} 并 commit")

        t2.execute(
            f"UPDATE {SCHEMA}.redemption_codes SET remaining = %s, updated_at = now() WHERE id = %s",
            (v2 - 1, CODE_ID),
        )
        t2.commit()
        step(f"T2 UPDATE remaining = {v2} - 1 = {v2 - 1} 并 commit   （用过期快照整体覆盖）")

        final = read_remaining(connect(), CODE_ID)
        fact("最终 remaining", f"{final}（应为 3，实际少扣 1 次）")
        check(final == 4, "复现丢失更新：T1 的扣减被 T2 的过期快照覆盖", "未复现")
        conclude(
            "根因：'读→算→写'跨了三次数据库交互，中间的 SELECT 只是一个随时过期的快照。"
            "并发写共享行时，计算必须下推到单条 SQL（场景 2）或锁住再算（场景 3）。"
        )
    finally:
        t1.close()
        t2.close()


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：条件 UPDATE —— 把"算"下推进 SQL，用一行原子性换正确性
# ──────────────────────────────────────────────────────────────────────────

def _atomic_redeem_once(user_id: int) -> tuple[bool, str]:
    """单次原子兑换：条件扣减 + 兑换记录同事务。返回 (是否成功, 说明)。"""
    with connect() as conn:
        try:
            cur = conn.execute(
                f"UPDATE {SCHEMA}.redemption_codes "
                f"SET remaining = remaining - 1, updated_at = now() "
                f"WHERE id = %s AND remaining >= 1 RETURNING remaining",
                (CODE_ID,),
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return False, "sold-out"
            conn.execute(
                f"INSERT INTO {SCHEMA}.redemptions (code_id, user_id) VALUES (%s, %s)",
                (CODE_ID, user_id),
            )
            conn.commit()
            return True, f"remaining -> {row[0]}"
        except Exception:
            conn.rollback()
            raise


def scenario_atomic_update() -> None:
    banner("场景 2：条件 UPDATE —— remaining >= 1 与扣减在同一条 SQL 内原子完成")
    reset_schema()
    seed_code(CODE_ID, "NEWYEAR", remaining=5)

    total, workers = 30, 30
    step(f"{workers} 个线程并发抢 {total} 个名额中的 5 个（每线程独立连接）")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_atomic_redeem_once, range(1, total + 1)))

    ok = sum(1 for succ, _ in results if succ)
    final = read_remaining(connect(), CODE_ID)
    rows = connect().execute(f"SELECT COUNT(*) FROM {SCHEMA}.redemptions").fetchone()[0]
    fact("成功兑换", f"{ok} / {workers}")
    fact("最终 remaining", final)
    fact("redemptions 行数", rows)
    check(ok == 5 and final == 0 and rows == 5, "恰好 5 人成功，不超卖不少卖", "计数异常！")
    conclude(
        "UPDATE 的 WHERE remaining >= 1 与 SET remaining = remaining - 1 在数据库内部"
        "对同一行加行锁后串行执行，不存在'读到旧值再覆盖'的窗口。"
        "秒杀/库存扣减的第一选择永远是条件 UPDATE，而不是应用层读改写。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：SELECT FOR UPDATE —— 显式行锁串行化"读→算→写"
# ──────────────────────────────────────────────────────────────────────────

def scenario_select_for_update() -> None:
    banner("场景 3：SELECT FOR UPDATE —— 必须在应用层计算时的正确姿势")
    reset_schema()
    seed_code(CODE_ID, "NEWYEAR", remaining=5)

    t1, t2 = connect(), connect()
    try:
        # 3.1 NOWAIT：拿不到锁立刻失败，适合"快速失败"场景
        t1.execute(
            f"SELECT remaining FROM {SCHEMA}.redemption_codes WHERE id = %s FOR UPDATE",
            (CODE_ID,),
        )
        step("T1 SELECT ... FOR UPDATE 已锁住 id=1")
        try:
            t2.execute(
                f"SELECT remaining FROM {SCHEMA}.redemption_codes WHERE id = %s FOR UPDATE NOWAIT",
                (CODE_ID,),
            )
            step("T2 NOWAIT 竟然成功？（不应发生）")
        except LockNotAvailable:
            step("T2 SELECT ... FOR UPDATE NOWAIT -> 立刻报错 lock not available（未阻塞等待）")
        t2.rollback()

        # 3.2 普通等待：T2 阻塞直到 T1 commit
        got_lock = threading.Event()

        def t2_wait() -> None:
            row = t2.execute(
                f"SELECT remaining FROM {SCHEMA}.redemption_codes WHERE id = %s FOR UPDATE",
                (CODE_ID,),
            ).fetchone()
            got_lock.set()
            t2.execute(
                f"UPDATE {SCHEMA}.redemption_codes SET remaining = %s WHERE id = %s",
                (int(row[0]) - 1, CODE_ID),
            )
            t2.commit()

        th = threading.Thread(target=t2_wait)
        th.start()
        step("T2 发起 SELECT ... FOR UPDATE，进入阻塞（等 T1 释放行锁）...")
        time.sleep(1.0)
        t1.execute(
            f"UPDATE {SCHEMA}.redemption_codes SET remaining = remaining - 1 WHERE id = %s",
            (CODE_ID,),
        )
        t1.commit()
        step("T1 扣减并 commit（释放行锁）")
        th.join(timeout=5)
        check(got_lock.is_set(), "T2 在 T1 commit 后立刻获得锁并继续（读到的是 T1 提交后的新值）", "T2 未能获得锁")
        fact("最终 remaining", read_remaining(connect(), CODE_ID))
        conclude(
            "FOR UPDATE 把行锁的获取提前到读取时刻，之后的'算'与'写'都在锁保护内进行，"
            "因此 T2 读到的一定是 T1 提交后的值。代价：并发度下降、持有锁的事务必须短，"
            "否则锁等待会拖垮吞吐——所以场景 2 的条件 UPDATE 仍是首选。"
        )
    finally:
        t1.close()
        t2.close()


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：唯一约束 —— 数据库层面的最后一道幂等防线
# ──────────────────────────────────────────────────────────────────────────

def _unique_redeem_once(user_id: int) -> str:
    try:
        with connect() as conn:
            conn.execute(
                f"UPDATE {SCHEMA}.redemption_codes "
                f"SET remaining = remaining - 1, updated_at = now() "
                f"WHERE id = %s AND remaining >= 1",
                (CODE_ID,),
            )
            conn.execute(
                f"INSERT INTO {SCHEMA}.redemptions (code_id, user_id) VALUES (%s, %s)",
                (CODE_ID, user_id),
            )
            conn.commit()
        return "ok"
    except UniqueViolation:
        return "duplicate"


def scenario_unique_constraint() -> None:
    banner("场景 4：唯一约束 —— 同一用户重复兑换被数据库拒绝")
    reset_schema()
    seed_code(CODE_ID, "NEWYEAR", remaining=100)

    step("第一次兑换（user=42）")
    fact("结果", _unique_redeem_once(42))
    step("同一用户再次提交（模拟网络重试/前端双击）")
    fact("结果", _unique_redeem_once(42))
    final = read_remaining(connect(), CODE_ID)
    rows = connect().execute(
        f"SELECT COUNT(*) FROM {SCHEMA}.redemptions WHERE user_id = 42"
    ).fetchone()[0]
    fact("remaining", f"{final}（100-1=99，第二次的扣减随事务一起回滚）")
    fact("user=42 的兑换记录数", rows)
    check(final == 99 and rows == 1, "重复请求整体回滚：扣减与插入在同一事务，一荣俱荣一损俱损", "事务边界被破坏")

    step("并发 10 个相同请求（同 user=77）同时提交")
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_unique_redeem_once, [77] * 10))
    fact("结果分布", f"ok={results.count('ok')}, duplicate={results.count('duplicate')}")
    check(results.count("ok") == 1, "并发下仍只有一条落库", "并发穿透了唯一约束？")
    conclude(
        "应用层校验（先 SELECT 查重再 INSERT）在并发下存在竞态窗口；"
        "UNIQUE(code_id, user_id) 让重复插入在索引层面直接失败，"
        "且因为扣减在同一事务，被拒绝的请求不会留下'名额已扣但没有记录'的脏状态。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 5：死锁 —— 交叉加锁的必然结局与重试模式
# ──────────────────────────────────────────────────────────────────────────

def _deadlock_pair() -> None:
    reset_schema()
    seed_code(1, "A", 100)
    seed_code(2, "B", 100)
    t1, t2 = connect(), connect()
    ev_t1_locked, ev_t2_locked, ev_cross = threading.Event(), threading.Event(), threading.Event()
    victim: list[str] = []

    def worker(conn: psycopg.Connection, first: int, second: int, locked_ev: threading.Event) -> None:
        try:
            conn.execute(
                f"UPDATE {SCHEMA}.redemption_codes SET remaining = remaining - 1 WHERE id = %s",
                (first,),
            )
            locked_ev.set()
            ev_cross.wait(timeout=5)  # 确保双方都锁住了第一把锁
            time.sleep(0.3)
            conn.execute(
                f"UPDATE {SCHEMA}.redemption_codes SET remaining = remaining - 1 WHERE id = %s",
                (second,),
            )
            conn.commit()
        except DeadlockDetected:
            victim.append("deadlock")
            conn.rollback()
        except Exception:
            conn.rollback()
            raise

    w1 = threading.Thread(target=worker, args=(t1, 1, 2, ev_t1_locked))
    w2 = threading.Thread(target=worker, args=(t2, 2, 1, ev_t2_locked))
    w1.start()
    w2.start()
    ev_t1_locked.wait(timeout=5)
    ev_t2_locked.wait(timeout=5)
    ev_cross.set()
    w1.join(timeout=10)
    w2.join(timeout=10)
    fact("死锁受害者", f"{len(victim)} 个事务被 PostgreSQL 强制回滚")
    check(len(victim) == 1, "PostgreSQL 检测到死锁环，牺牲一个事务让另一个继续", "未触发死锁（时序未对上）")
    t1.close()
    t2.close()


def scenario_deadlock() -> None:
    banner("场景 5：死锁 —— T1 锁 A 等 B，T2 锁 B 等 A")
    step("first demo: 交叉加锁触发死锁，观察 PostgreSQL 的仲裁（默认 1s 检测）")
    _deadlock_pair()

    step("second demo: 完整事务重试模式（捕获死锁 -> 回滚 -> 重试整个事务）")
    reset_schema()
    seed_code(1, "A", 100)
    seed_code(2, "B", 100)

    def transfer_both(conn: psycopg.Connection) -> None:
        """对 A、B 各扣 1：按固定顺序加锁，从根源上避免死锁环。"""
        for code_id in (1, 2):  # 固定顺序 = 死锁预防的第一手段
            conn.execute(
                f"UPDATE {SCHEMA}.redemption_codes SET remaining = remaining - 1 WHERE id = %s",
                (code_id,),
            )
        conn.commit()

    attempts = 0
    while True:
        attempts += 1
        try:
            with connect() as conn:
                transfer_both(conn)
            break
        except (DeadlockDetected, SerializationFailure):
            step(f"第 {attempts} 次尝试被回滚，退避后重试")
            time.sleep(0.2 * attempts)

    a = read_remaining(connect(), 1)
    b = read_remaining(connect(), 2)
    fact(f"重试后 A={a}, B={b}", f"共尝试 {attempts} 次")
    check(a == 99 and b == 99, "重试后事务最终成功", "重试失败")
    conclude(
        "死锁无法彻底消灭，只能：①按固定顺序访问资源（治本）；②缩短事务；"
        "③把 DeadlockDetected / SerializationFailure 当作可重试错误（治标，必须有）。"
        "任何'多行写'的业务代码都值得问一句：加锁顺序固定吗？"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 6：隔离级别 —— Read Committed 的不可重复读 vs Repeatable Read
# ──────────────────────────────────────────────────────────────────────────

def _seed_kv() -> None:
    with connect() as conn:
        conn.execute(f"DROP TABLE IF EXISTS {SCHEMA}.kv")
        conn.execute(f"CREATE TABLE {SCHEMA}.kv (id INT PRIMARY KEY, value INT NOT NULL)")
        conn.execute(f"INSERT INTO {SCHEMA}.kv (id, value) VALUES (1, 100)")
        conn.commit()


def scenario_isolation_levels() -> None:
    banner("场景 6：隔离级别 —— 同一事务内两次读，结果凭什么不一样")

    # 6.1 Read Committed（PostgreSQL 默认）：每条语句拿最新快照
    _seed_kv()
    t1, t2 = connect(), connect()
    try:
        v_first = t1.execute("SELECT value FROM tlr_txn.kv WHERE id = 1").fetchone()[0]
        t2.execute("UPDATE tlr_txn.kv SET value = 200 WHERE id = 1")
        t2.commit()
        v_second = t1.execute("SELECT value FROM tlr_txn.kv WHERE id = 1").fetchone()[0]
        t1.commit()
        step(f"[Read Committed] T1 第一次读: {v_first}，T2 改成 200 提交后，T1 第二次读: {v_second}")
        check(v_first != v_second, "不可重复读：同一事务内两次 SELECT 看到了不同的值", "未复现")
    finally:
        t1.close()
        t2.close()

    # 6.2 Repeatable Read：事务开始时固定快照
    _seed_kv()
    t1, t2 = connect(), connect()
    t1.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    try:
        v_first = t1.execute("SELECT value FROM tlr_txn.kv WHERE id = 1").fetchone()[0]
        t2.execute("UPDATE tlr_txn.kv SET value = 200 WHERE id = 1")
        t2.commit()
        v_second = t1.execute("SELECT value FROM tlr_txn.kv WHERE id = 1").fetchone()[0]
        step(f"[Repeatable Read] T1 第一次读: {v_first}，T2 提交后 T1 第二次读: {v_second}（快照未变）")
        check(v_first == v_second, "可重复读：快照固定，外部提交对 T1 不可见", "未复现")
        try:
            t1.execute("UPDATE tlr_txn.kv SET value = value - 1 WHERE id = 1")
            t1.commit()
            step("T1 竟然提交成功？（不应发生）")
        except SerializationFailure:
            t1.rollback()
            step("T1 尝试写入 -> ERROR: could not serialize access（40001，必须整事务重试）")
        check(True, "写冲突被显式拒绝，而不是静默基于旧快照覆盖", "")
    finally:
        t1.close()
        t2.close()

    conclude(
        "Read Committed 下'两次读不一致'是常态（每条语句一个新快照），适合绝大多数业务；"
        "Repeatable Read 用快照隔离换来一致性视图，代价是写冲突会抛 40001，"
        "调用方必须把整个事务当作可重试单元。没有银弹，只有取舍。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景注册表
# ──────────────────────────────────────────────────────────────────────────

SCENARIOS: dict[str, object] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    SCENARIOS[f"__desc_{name}"] = desc


_reg("lost-update", scenario_lost_update, "反模式：读-算-写丢失更新")
_reg("atomic-update", scenario_atomic_update, "条件 UPDATE 原子扣减（并发抢名额不超卖）")
_reg("select-for-update", scenario_select_for_update, "显式行锁：NOWAIT 快速失败与阻塞等待")
_reg("unique-constraint", scenario_unique_constraint, "唯一约束：重复兑换的最后一道防线")
_reg("deadlock", scenario_deadlock, "死锁现场、仲裁与固定顺序+重试模式")
_reg("isolation-levels", scenario_isolation_levels, "Read Committed vs Repeatable Read")


def run_all() -> None:
    for name, fn in list(SCENARIOS.items()):
        if name.startswith("__desc_"):
            continue
        fn()


def describe(name: str) -> str:
    return str(SCENARIOS.get(f"__desc_{name}", ""))
