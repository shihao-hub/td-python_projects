"""2PC（两阶段提交）协调者：整个协议 = 投票+ 执行。

对应文章要点：
- 阶段一 Prepare：广播事务内容，收集 Yes/No 投票（任一 No 或超时未响应都算失败）
- 阶段二 Commit/Rollback：按投票结果统一指挥提交或回滚，收到全部 Ack 后事务完成
- 协调者有自己的超时机制；但【参与者没有】-> 协调者崩溃时参与者永久持锁
  （同步阻塞 + 单点问题的根源）
- 故障注入 crash_after_commit_to=N：模拟协调者 commit 广播中途崩溃，
  只有前 N 个参与者收到指令 -> 演示"数据不一致 + 单点问题"两大缺陷
"""

import asyncio

from apps.distributed_transaction.participant import CoordinatorCrashed, Participant

# 一次事务里单个参与者要执行的操作：[(字段名, 增量), ...]
Ops = list[tuple[str, int]]


class Coordinator2PC:
    """2PC 协调者（事务管理器）。"""

    def __init__(
        self,
        participants: list[Participant],
        *,
        tag: str,                                 # 日志前缀，如 "2PC-场景1"
        response_timeout: float = 1.0,            # 协调者等待响应的超时（2PC 只有协调者有超时）
        crash_after_commit_to: int | None = None,  # 故障注入：commit 发给前 N 个参与者后崩溃
    ) -> None:
        self.participants = participants
        self.tag = tag
        self.response_timeout = response_timeout
        self.crash_after_commit_to = crash_after_commit_to

    def _log(self, msg: str, indent: int = 1) -> None:
        print(f"[{self.tag}] {'  ' * indent}{msg}")

    async def run(self, plan: dict[str, Ops]) -> bool:
        """执行一次完整事务。plan: 参与者名 -> 操作列表。返回事务最终是否提交。"""
        # ════════ 阶段一：Prepare（投票）════════
        self._log("=== 阶段一 Prepare：广播事务内容，各参与者投票 ===", indent=0)
        votes = await self._collect_votes(plan)

        if not all(votes):
            # 任一 No 票或超时 -> 协调者决定中断。即使其他参与者都已执行成功，
            # 也只能全体回滚 —— 这就是文章说的"过于保守"：
            # 没有完善的容错机制，任意一个节点失败都会导致整个事务失败
            self._log("存在 No 票或超时未响应 -> 决定中断事务，进入阶段二 Rollback")
            await asyncio.gather(*(p.rollback() for p in self.participants))
            self._log("收到全部回滚 Ack，事务中断完成")
            return False

        # ════════ 阶段二：Commit（执行）════════
        self._log("=== 阶段二 Commit：全票通过，广播 commit 指令 ===", indent=0)
        if self.crash_after_commit_to is not None:
            # 故障注入：commit 只发给了前 N 个参与者，协调者随即崩溃。
            # 后果一（数据不一致）：收到指令的提交了，没收到的悬在半空；
            # 后果二（单点问题）：悬着的参与者永久持锁干等，2PC 体系内无解，
            # 因为参与者没有超时机制，只能人工介入恢复
            for i, p in enumerate(self.participants):
                if i >= self.crash_after_commit_to:
                    self._log(f"!! 协调者崩溃！[{p.name}] 永远等不到指令 ...")
                    raise CoordinatorCrashed("commit 广播中途崩溃")
                await p.commit()
            return True  # 全发完才轮到这行（注入值 >= 参与者数时相当于没崩）

        await asyncio.gather(*(p.commit() for p in self.participants))
        self._log("收到全部提交 Ack，事务完成")
        return True

    async def _collect_votes(self, plan: dict[str, Ops]) -> list[bool]:
        """并行向所有参与者发 Prepare 并等响应；超时/异常一律按 No 处理（保守策略）。"""

        async def ask(p: Participant) -> bool:
            self._log(f"协调者 -> [{p.name}]: Prepare(事务内容)")
            return await p.prepare(plan[p.name])

        # wait_for 实现"协调者的超时机制"：等不到响应就当失败
        results = await asyncio.gather(
            *(asyncio.wait_for(ask(p), self.response_timeout) for p in self.participants),
            return_exceptions=True,
        )
        votes: list[bool] = []
        for p, r in zip(self.participants, results):
            if isinstance(r, BaseException):
                self._log(f"[{p.name}] 超时未响应 -> 视为 No（保守策略：一个失败全体失败）")
                votes.append(False)
            else:
                votes.append(r)
        return votes
