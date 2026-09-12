"""六个场景：浮点精度 / 幂等扣费 / 原子性 / 补偿 / 重复退款 / 对账。"""

from __future__ import annotations

from decimal import Decimal

from apps.billing_compensation import billing
from apps.billing_compensation.db import connect, reset_schema
from apps.common.lab import banner, check, conclude, fact, step

U = 42


def scenario_float_vs_decimal() -> None:
    banner("场景 1：为什么金额必须是 Decimal / NUMERIC")
    step("Python float：")
    print(f"      0.1 + 0.2 == 0.3          -> {0.1 + 0.2 == 0.3}   （{0.1 + 0.2!r}）")
    total = 0.0
    for _ in range(100_000):
        total += 0.01
    print(f"      10 万次累加 0.01           -> {total!r}   （凭空多出 {(total - 1000):.10f}）")
    step("Decimal：")
    d = sum(Decimal("0.01") for _ in range(100_000))
    print(f"      10 万次累加 Decimal 0.01   -> {d}   （分毫不差）")

    with connect() as conn:
        float_r = conn.execute("SELECT 0.1::float8 + 0.2::float8 = 0.3::float8").fetchone()[0]
        num_r = conn.execute("SELECT 0.1::numeric + 0.2::numeric = 0.3::numeric").fetchone()[0]
    fact("PostgreSQL float8 0.1+0.2=0.3", float_r)
    fact("PostgreSQL numeric 0.1+0.2=0.3", num_r)
    check((not float_r) and num_r and d == Decimal("1000.00"),
          "float 二进制无法精确表示十进制小数；NUMERIC/Decimal 是十进制精确存储", "未复现精度问题")
    conclude(
        "金额字段 = NUMERIC(p,s) + Python Decimal，接口层 Pydantic 用 condecimal。"
        "float 的误差在单笔里看不见，在对账汇总时必然爆雷——'账平不了'的经典根因。"
    )


def scenario_idempotent_charge() -> None:
    banner("场景 2：幂等扣费 —— 同一 idem_key 的重复上报只扣一次")
    reset_schema()
    billing.ensure_wallet(U)
    billing.grant(U, Decimal("100.00"))

    r1 = billing.charge(U, "task:render:9001", Decimal("9.90"))
    fact("第一次 charge", r1)
    try:
        billing.charge(U, "task:render:9001", Decimal("9.90"))
        check(False, "不应到达", "")
    except billing.DuplicateRequest as dup:
        fact("第二次 charge", f"DuplicateRequest -> 首次快照 {dup.snapshot}")
    fact("余额", billing.wallet_balance(U))
    fact("debit 分录数", billing.entry_count(U) - 1)  # 减去 grant 的 credit
    check(billing.wallet_balance(U) == Decimal("90.10"), "余额只扣了一次：100 - 9.90", "重复扣费！")
    conclude(
        "幂等键三键合一：idempotency_key（防重复）= group_key（补偿定位）= 业务任务标识。"
        "任务重试、消息重放、用户双击，全部被同一把锁挡住。"
    )


def scenario_atomic_charge() -> None:
    banner("场景 3：扣费的原子性 —— 余额不足零副作用；中途故障零残留")
    reset_schema()
    billing.ensure_wallet(U)
    billing.grant(U, Decimal("10.00"))

    step("3.1 余额不足")
    try:
        billing.charge(U, "task:big:1", Decimal("99.00"))
    except billing.InsufficientBalance as exc:
        fact("拒绝", exc)
    fact("余额（应仍为 10.00）", billing.wallet_balance(U))
    fact("分录数（应只有 grant 一条 credit）", billing.entry_count(U))

    step("3.2 扣减后数据库故障（同事务整体回滚）")
    try:
        billing.charge(U, "task:crash:1", Decimal("5.00"), fail_after="db")
    except RuntimeError as exc:
        fact("捕获", exc)
    fact("余额（应仍为 10.00）", billing.wallet_balance(U))
    fact("分录数（故障事务零残留）", billing.entry_count(U))
    ok = billing.wallet_balance(U) == Decimal("10.00") and billing.entry_count(U) == 1
    check(ok, "拒绝与故障都没有留下任何半成品状态", "事务边界泄漏！")
    conclude(
        "钱包扣减、批次消耗、账本分录必须在同一个数据库事务里。"
        "任何一步失败整体回滚——这是'扣费'区别于普通 UPDATE 的地方：它是有不变量约束的群组写。"
    )


def scenario_refund_compensation() -> None:
    banner("场景 4：补偿配对 —— 扣费后业务失败，按原操作精确退回")
    reset_schema()
    billing.ensure_wallet(U)
    billing.grant(U, Decimal("50.00"))

    r = billing.charge(U, "task:ai:777", Decimal("30.00"))
    fact("charge", r)
    fact("扣费后批次", billing.lot_snapshot(U))

    step("业务侧执行 AI 任务 -> 失败 -> 触发补偿 refund(group_key)")
    back = billing.refund(r["group_key"], user_id=U, note="ai task failed")
    fact("refund", back)
    fact("余额（应回到 50.00）", billing.wallet_balance(U))
    fact("退款后批次（consumed 回补、exhausted 恢复 active）", billing.lot_snapshot(U))
    entries = billing.reconcile(U)
    fact("对账", entries)
    check(billing.wallet_balance(U) == Decimal("50.00") and entries["ok"],
          "补偿 = 原操作的镜像：金额、批次、账本全部可逆", "补偿不守恒！")
    conclude(
        "每个扣费方法都要有配对的补偿方法（charge/refund、grant/revoke）。"
        "补偿不是'往钱包加钱'这么粗，而是逐分录、逐批次的逆向操作，"
        "related_group_key 把补偿指回原操作，审计链路完整。"
    )


def scenario_double_refund_guard() -> None:
    banner("场景 5：重复退款防护 —— 同一原操作的第二次退款被唯一约束拦截")
    reset_schema()
    billing.ensure_wallet(U)
    billing.grant(U, Decimal("20.00"))
    r = billing.charge(U, "task:x:1", Decimal("8.00"))

    billing.refund(r["group_key"], user_id=U)
    fact("第一次退款后余额", billing.wallet_balance(U))
    try:
        billing.refund(r["group_key"], user_id=U)
        check(False, "不应到达", "")
    except billing.DuplicateRequest as dup:
        fact("第二次退款", f"DuplicateRequest -> {dup}")
    fact("最终余额（应仍为 20.00）", billing.wallet_balance(U))
    check(billing.wallet_balance(U) == Decimal("20.00"), "退款幂等：退一次可以，退两次不行", "重复退款！")
    conclude(
        "补偿自身的幂等常被忽略：'重试触发的第二次退款'与首次退款并发或先后到达时，"
        "靠 refund:{原group_key} 的唯一约束兜底。补偿配对表要写成显式数据结构，"
        "而不是散落在注释里的口头约定。"
    )


def scenario_reconcile() -> None:
    banner("场景 6：对账 —— 账本守恒是最后的底线")
    reset_schema()
    billing.ensure_wallet(U)
    billing.grant(U, Decimal("100.00"))
    billing.grant(U, Decimal("60.00"))
    c1 = billing.charge(U, "task:a:1", Decimal("33.33"))
    billing.charge(U, "task:b:2", Decimal("16.67"))
    billing.refund(c1["group_key"], user_id=U)
    billing.charge(U, "task:c:3", Decimal("50.00"))

    result = billing.reconcile(U)
    fact("wallets.balance", result["wallet_balance"])
    fact("sum(credit) - sum(debit)", result["expected"])
    fact("守恒", result["ok"])
    check(result["ok"], "每一分钱都同时在钱包与账本上有一致的记录", "账不平！")
    conclude(
        "对账是系统层面的安全网：定期跑 sum(credit)-sum(debit) == balance，"
        "配合不可变账本（禁 UPDATE/DELETE），任何代码 bug 都会在对账时现形而非沉默。"
        "金额方向恒正 + Decimal + 不可变分录，是计费系统的三块基石。"
    )


SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("float-vs-decimal", scenario_float_vs_decimal, "float 精度事故 vs Decimal/NUMERIC")
_reg("idempotent-charge", scenario_idempotent_charge, "幂等扣费：重复上报只扣一次")
_reg("atomic-charge", scenario_atomic_charge, "余额不足零副作用；中途故障零残留")
_reg("refund-compensation", scenario_refund_compensation, "补偿配对：精确逆向退款")
_reg("double-refund-guard", scenario_double_refund_guard, "重复退款被唯一约束拦截")
_reg("reconcile", scenario_reconcile, "对账：账本与钱包守恒")


def run_all() -> None:
    for fn in SCENARIOS.values():
        fn()
