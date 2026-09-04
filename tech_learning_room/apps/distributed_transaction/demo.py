"""2PC / 3PC 对照演示：以"账户A 向账户B 转账 30"为例，把文章要点逐个演出来。

运行方式（纯标准库，无任何外部依赖）：

    uv run python -m apps.distributed_transaction

七个场景：
  2PC：1 成功提交 / 2 投票 No 全体回滚 / 3 参与者宕机 -> 协调者保守中断 /
       4 协调者 commit 广播中途崩溃 -> 数据不一致 + 单点永久阻塞
  3PC：5 成功提交 / 6 CanCommit 阶段就投 No -> 中断零成本 /
       7 协调者 DoCommit 前崩溃 -> 参与者超时自动提交，不再永久阻塞
"""

import asyncio

from apps.distributed_transaction.coordinator_2pc import Coordinator2PC
from apps.distributed_transaction.coordinator_3pc import Coordinator3PC
from apps.distributed_transaction.participant import CoordinatorCrashed, Participant

# 转账计划：账户A 转出 30，账户B 转入 30（两个参与者各管一个账户）
PLAN_TRANSFER: dict[str, list[tuple[str, int]]] = {
    "账户A": [("balance", -30)],
    "账户B": [("balance", +30)],
}


def fresh_accounts(
    tag: str,
    *,
    vote_no: str | None = None,
    crash: str | None = None,
    timeout: float | None = None,
) -> list[Participant]:
    """每个场景都用全新的两台"数据库节点"：A=100，B=100。"""

    def flags(name: str) -> dict:
        return {
            "vote_no": vote_no == name,        # 指定谁投票必回 No
            "crash_at_prepare": crash == name,  # 指定谁收到 Prepare 就宕机
            "timeout": timeout,                 # 3PC 参与者超时；2PC 场景保持 None
        }

    ps = [
        Participant("账户A", {"balance": 100}, **flags("账户A")),
        Participant("账户B", {"balance": 100}, **flags("账户B")),
    ]
    print(f"\n{'=' * 64}\n[{tag}]  A=100, B=100，A 向 B 转账 30\n{'=' * 64}")
    return ps


def show(ps: list[Participant], result: str) -> None:
    """打印场景结束后的各参与者最终状态 + 结论。"""
    for p in ps:
        print(f"    {p.snapshot()}")
    print(f"    结论: {result}")


# ──────────────────────────────────────────
# 2PC 场景
# ──────────────────────────────────────────


async def scenario_2pc_success() -> None:
    """场景 1：全员正常 -> 投票 + 执行，两阶段完成提交。"""
    ps = fresh_accounts("2PC-场景1 全员正常，成功提交")
    ok = await Coordinator2PC(ps, tag="2PC-场景1").run(PLAN_TRANSFER)
    show(ps, "提交成功 A=70, B=130；两阶段提交做的就是两件事：投票、执行" if ok else "失败")


async def scenario_2pc_vote_no() -> None:
    """场景 2：B 投 No -> 全体回滚（已执行事务的 A 靠 Undo 日志恢复）。"""
    ps = fresh_accounts("2PC-场景2 参与者投 No，全体回滚", vote_no="账户B")
    ok = await Coordinator2PC(ps, tag="2PC-场景2").run(PLAN_TRANSFER)
    show(ps, "A 的半成品事务被 Undo 回滚，数据无残留" if not ok else "意外提交")


async def scenario_2pc_participant_timeout() -> None:
    """场景 3：A 宕机不响应 -> 协调者只能靠自身超时保守中断整个事务。"""
    ps = fresh_accounts("2PC-场景3 参与者宕机，协调者保守中断", crash="账户A")
    ok = await Coordinator2PC(ps, tag="2PC-场景3", response_timeout=0.5).run(PLAN_TRANSFER)
    show(
        ps,
        "B 本可成功却因 A 宕机整体失败 —— 文章所说"
        "'过于保守：任意节点失败 => 整个事务失败'" if not ok else "意外提交",
    )


async def scenario_2pc_coordinator_crash() -> None:
    """场景 4：协调者 commit 只发给 A 就崩溃 -> A 已提交、B 永久 PREPARED 持锁。"""
    ps = fresh_accounts("2PC-场景4 协调者崩溃：数据不一致 + 单点阻塞")
    try:
        await Coordinator2PC(ps, tag="2PC-场景4", crash_after_commit_to=1).run(PLAN_TRANSFER)
    except CoordinatorCrashed:
        print("[2PC-场景4]    -> 事务悬在半空：A 已提交、B 永远等不到指令")
        print("[2PC-场景4]    -> 30 元凭空消失（数据不一致）+ B 资源锁死（单点问题）")
        print("[2PC-场景4]    -> 2PC 参与者没有超时机制，只能人工介入恢复")
    show(ps, "部分提交、部分悬空：经典的数据不一致现场")


# ──────────────────────────────────────────
# 3PC 场景
# ──────────────────────────────────────────


async def scenario_3pc_success() -> None:
    """场景 5：三阶段全绿。对照场景 1，看多出来的 CanCommit / PreCommit 两步。"""
    ps = fresh_accounts("3PC-场景5 全员正常，成功提交", timeout=1.5)
    ok = await Coordinator3PC(ps, tag="3PC-场景5").run(PLAN_TRANSFER)
    show(
        ps,
        "提交成功 A=70, B=130；PreCommit 缓冲保证最终提交前各节点状态一致"
        if ok
        else "失败",
    )


async def scenario_3pc_can_commit_no() -> None:
    """场景 6：CanCommit 阶段 B 就回答 No -> 还没锁任何资源，中断零成本。"""
    ps = fresh_accounts("3PC-场景6 CanCommit 就投 No，中断零成本", vote_no="账户B", timeout=1.5)
    ok = await Coordinator3PC(ps, tag="3PC-场景6").run(PLAN_TRANSFER)
    show(
        ps,
        "对比 2PC-场景2：异议在锁资源【之前】就暴露，无需回滚半成品事务"
        if not ok
        else "意外提交",
    )


async def scenario_3pc_coordinator_crash() -> None:
    """场景 7：协调者 DoCommit 前崩溃 -> 参与者超时自动提交，不再永久阻塞。"""
    ps = fresh_accounts("3PC-场景7 协调者崩溃，参与者超时自动提交", timeout=0.8)
    try:
        await Coordinator3PC(ps, tag="3PC-场景7", crash_before_do_commit=True).run(PLAN_TRANSFER)
    except CoordinatorCrashed:
        print("[3PC-场景7]    -> 协调者崩溃，doCommit/abort 均未发出")
        print("[3PC-场景7]    -> 参与者靠自己的超时计时器继续等待 ...")
        # 等待参与者的超时计时器触发"自动提交"
        await asyncio.sleep(1.2)
    show(
        ps,
        "无永久阻塞（优于 2PC-场景4）；但若各参与者超时判定不一致仍可能数据不一致"
        " —— 3PC 并未完全解决数据不一致问题",
    )


async def _run_all() -> None:
    await scenario_2pc_success()
    await scenario_2pc_vote_no()
    await scenario_2pc_participant_timeout()
    await scenario_2pc_coordinator_crash()
    await scenario_3pc_success()
    await scenario_3pc_can_commit_no()
    await scenario_3pc_coordinator_crash()
    print("\n== 全部 7 个场景演示结束 ==")


def main() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
