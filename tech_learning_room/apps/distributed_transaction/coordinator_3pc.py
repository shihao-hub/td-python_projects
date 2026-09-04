"""3PC（三阶段提交）协调者：CanCommit -> PreCommit -> DoCommit。

对应文章要点：
- 把 2PC 的"准备"阶段一拆为二：
    CanCommit（纯询问，不锁资源）+ PreCommit（缓冲阶段：执行事务、写日志、锁资源）
- PreCommit 是缓冲：保证进入最终 DoCommit 之前，各参与节点状态一致
  （全部写好 Undo/Redo 日志、持锁待命），降低最后阶段翻车概率
- 参与者带超时机制：即使协调者崩溃，参与者超时后也能自行提交、释放资源，
  大幅缩小 2PC 的阻塞时间和范围；但【并未完全解决数据不一致问题】
"""

import asyncio

from apps.distributed_transaction.participant import CoordinatorCrashed, Participant

Ops = list[tuple[str, int]]


class Coordinator3PC:
    """3PC 协调者（事务管理器）。"""

    def __init__(
        self,
        participants: list[Participant],
        *,
        tag: str,
        response_timeout: float = 1.0,
        crash_before_do_commit: bool = False,  # 故障注入：DoCommit 广播前协调者崩溃
    ) -> None:
        self.participants = participants
        self.tag = tag
        self.response_timeout = response_timeout
        self.crash_before_do_commit = crash_before_do_commit

    def _log(self, msg: str, indent: int = 1) -> None:
        print(f"[{self.tag}] {'  ' * indent}{msg}")

    async def run(self, plan: dict[str, Ops]) -> bool:
        """执行一次完整事务。plan: 参与者名 -> 操作列表。返回事务最终是否提交。"""

        # ════════ 阶段一：CanCommit（纯询问，不动资源）════════
        self._log("=== 阶段一 CanCommit：轻量询问各节点能否执行事务（不锁资源）===", indent=0)
        answers = await self._collect("CanCommit", lambda p: p.can_commit(plan[p.name]))
        if not all(answers):
            # 此刻所有参与者都还没锁资源，中断几乎零成本
            # （对比 2PC：要等 prepare 执行完写完日志才可能发现不行，还得回滚）
            self._log("存在 No -> 广播 abort（此刻大家还没锁资源，中断零成本）")
            await asyncio.gather(*(p.rollback() for p in self.participants))
            return False

        # ════════ 阶段二：PreCommit（缓冲阶段）════════
        self._log("=== 阶段二 PreCommit：各节点执行事务+写 Undo/Redo+锁资源 ===", indent=0)
        # 复用参与者的 prepare 逻辑（2PC/3PC 的"执行事务"动作本身相同）
        acks = await self._collect("PreCommit", lambda p: p.prepare(plan[p.name]))
        if not all(acks):
            self._log("存在 No/超时 -> 广播 abort，各参与者用 Undo 回滚并释放锁")
            await asyncio.gather(*(p.rollback() for p in self.participants))
            return False

        # ════════ 阶段三：DoCommit（正式提交）════════
        self._log("=== 阶段三 DoCommit：PreCommit 全部 Ack，广播正式提交 ===", indent=0)
        if self.crash_before_do_commit:
            # 故障注入：协调者在此崩溃，doCommit/abort 都发不出去。
            # 参与者不会像 2PC 那样永久持锁：它们有自己的超时机制，
            # 超时后自动提交并释放资源（实际效果见 demo 场景 7）
            self._log("!! 协调者崩溃！doCommit/abort 均无法发出 ...")
            raise CoordinatorCrashed("DoCommit 广播前崩溃")

        await asyncio.gather(*(p.commit() for p in self.participants))
        self._log("收到全部提交 Ack，事务完成")
        return True

    async def _collect(self, phase: str, ask) -> list[bool]:
        """并行询问所有参与者；超时/异常一律按 No 处理。"""

        async def wrapped(p: Participant) -> bool:
            self._log(f"协调者 -> [{p.name}]: {phase}")
            return await ask(p)

        results = await asyncio.gather(
            *(asyncio.wait_for(wrapped(p), self.response_timeout) for p in self.participants),
            return_exceptions=True,
        )
        votes: list[bool] = []
        for p, r in zip(self.participants, results):
            if isinstance(r, BaseException):
                self._log(f"[{p.name}] 超时未响应 -> 视为 No")
                votes.append(False)
            else:
                votes.append(r)
        return votes
