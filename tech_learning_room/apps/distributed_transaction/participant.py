"""参与者（Participant）：模拟分布式事务中的一台数据库节点。

对应文章中参与者的职责：
- 2PC：收到 Prepare -> 执行本地事务 + 写 Undo/Redo 日志 + 加锁（但事务不提交），
  然后干等协调者的 Commit/Rollback 指令。2PC 的参与者【没有】自己的超时机制，
  协调者一旦崩溃就会永久持锁 —— 这正是"同步阻塞 + 单点问题"的根源。
- 3PC：参与者在 PreCommit 成功后启动自己的超时计时器，
  等不到 doCommit/abort 指令时超时【自动提交】，主动释放资源 —— 3PC 的关键改进。

实现方式：内存 dict 模拟数据库表，locked 标志模拟事务期间占用的资源锁，
asyncio.sleep 模拟网络/磁盘耗时。无任何外部依赖。
"""

import asyncio


class CoordinatorCrashed(Exception):
    """故障注入专用异常：模拟协调者在关键时刻崩溃（由 demo 捕获后展示后果）。"""


class Participant:
    """最小化的"数据库节点"：只支持按字段做增减的转账类事务。"""

    def __init__(
        self,
        name: str,
        data: dict[str, int],
        *,
        vote_no: bool = False,           # 故障注入：本地检查不通过，投票必回 No
        crash_at_prepare: bool = False,  # 故障注入：节点收到 Prepare 就宕机（永不响应）
        timeout: float | None = None,    # 3PC 参与者超时秒数；2PC 传 None（无超时=可能永久阻塞）
    ) -> None:
        self.name = name
        self.data = dict(data)              # 模拟本地库表，如 {"balance": 100}
        self.undo_log: dict[str, int] = {}  # Undo 日志：记录修改前的值，用于回滚
        self.redo_log: dict[str, int] = {}  # Redo 日志：记录修改后的值，用于提交后写入数据文件
        self.locked = False                 # 模拟事务期间占用的资源锁
        self.state = "INIT"                 # 状态机：INIT -> PREPARED -> COMMITTED / ABORTED
        self.vote_no = vote_no
        self.crash_at_prepare = crash_at_prepare
        self.timeout = timeout
        self._timer: asyncio.Task | None = None

    # ──────────────────────────────────────────
    # 3PC 阶段一：CanCommit（纯询问，不动任何资源）
    # ──────────────────────────────────────────
    async def can_commit(self, ops: list[tuple[str, int]]) -> bool:
        """对应 3PC 的 CanCommit：只做本地预检查，不执行事务、不写日志、不锁资源。"""
        await asyncio.sleep(0.03)  # 模拟网络往返
        if self.vote_no or not all(self.data[k] + d >= 0 for k, d in ops):
            # 此刻还没占任何资源，回答 No 的代价为零 —— 这就是 3PC 拆出 CanCommit 的意义
            print(f"      [{self.name}] CanCommit 检查未通过 -> No（未锁任何资源，中断零成本）")
            return False
        print(f"      [{self.name}] CanCommit 检查通过 -> Yes，进入预备状态")
        return True

    # ──────────────────────────────────────────
    # 2PC 阶段一 / 3PC 阶段二：执行事务（写日志、加锁、但不提交）
    # ──────────────────────────────────────────
    async def prepare(self, ops: list[tuple[str, int]]) -> bool:
        """对应 2PC 的 Prepare / 3PC 的 PreCommit：执行本地事务并投票。"""
        if self.crash_at_prepare:
            # 模拟节点宕机：永不响应，逼协调者走"超时视为失败"的保守分支。
            # 这个 sleep 会被协调者的 wait_for 超时取消，不会真的睡 9999 秒
            await asyncio.sleep(9999)
            return False  # 不可达

        await asyncio.sleep(0.05)  # 模拟网络 + 本地执行耗时

        if self.vote_no:
            print(f"      [{self.name}] 本地检查失败 -> 投 No（不执行事务、不占锁）")
            return False

        # 执行事务：先写 Undo/Redo 日志，再修改内存数据（注意：此刻事务尚未提交）
        for key, delta in ops:
            old = self.data[key]
            self.undo_log[key] = old          # Undo：记录修改前的数据，用于回滚
            self.redo_log[key] = old + delta  # Redo：记录修改后的数据，用于提交后写入数据文件
            self.data[key] = old + delta      # 本地修改，随时可用 Undo 恢复

        self.locked = True  # 占住资源锁：提交/回滚之前，其他事务无法访问这些数据
        self.state = "PREPARED"
        print(f"      [{self.name}] 执行事务+写 Undo/Redo+加锁 -> 投 Yes（state=PREPARED）")

        if self.timeout is not None:
            self._arm_timeout()  # 3PC 专属：PreCommit 成功即启动参与者自己的超时计时器
        return True

    # ──────────────────────────────────────────
    # 2PC 阶段二 / 3PC 阶段三：提交或回滚
    # ──────────────────────────────────────────
    async def commit(self, *, by_timeout: bool = False) -> bool:
        """正式提交：Redo 生效、数据保留，释放锁。by_timeout 标记 3PC 超时自动提交。"""
        self._cancel_timer()  # 正常收到指令，先取消"超时自动提交"计时器
        if by_timeout:
            # 3PC 关键改进：等不到协调者指令时，参与者靠自己的超时机制提交并释放资源，
            # 避免 2PC 中"协调者一挂、参与者永久持锁"的阻塞
            print(f"      [{self.name}] !! 等待 doCommit 超时 -> 参与者自行提交并释放锁（3PC 特有）")
        self.undo_log.clear()  # 已提交，Undo 不再需要
        self.redo_log.clear()  # Redo 数据已在提交时"写入数据文件"
        self.locked = False    # 释放整个事务期间占用的资源
        self.state = "COMMITTED"
        if not by_timeout:
            print(f"      [{self.name}] 正式提交，释放锁 -> Ack（state=COMMITTED）")
        return True

    async def rollback(self) -> bool:
        """回滚：用 Undo 日志恢复数据（若执行过事务），释放锁。"""
        self._cancel_timer()
        if self.crash_at_prepare and self.state == "INIT":
            # 该节点在收到 Prepare 时就宕机了：真实场景它收不到 abort 消息；
            # 恢复后发现本地没有任何事务日志，天然无事可做 —— 这里只标记状态
            print(f"      [{self.name}] （宕机中，abort 消息不可达；恢复后无日志可回滚）")
            self.state = "ABORTED"
            return False
        if self.state == "PREPARED":
            self.data.update(self.undo_log)  # 用修改前的数据覆盖回去
            print(f"      [{self.name}] 用 Undo 日志回滚，释放锁 -> Ack（state=ABORTED）")
        else:
            # 3PC 在 CanCommit 阶段就中断时，参与者从未执行过事务
            print(f"      [{self.name}] 未执行过事务，直接中断 -> Ack（state=ABORTED）")
        self.undo_log.clear()
        self.redo_log.clear()
        self.locked = False
        self.state = "ABORTED"
        return True

    # ──────────────────────────────────────────
    # 3PC 参与者超时机制（2PC 不会启用：timeout=None 时根本不会 arm）
    # ──────────────────────────────────────────
    def _arm_timeout(self) -> None:
        """3PC：PreCommit 后若 timeout 秒内收不到 doCommit/abort，则自动提交。"""
        self._timer = asyncio.create_task(self._timeout_worker())

    async def _timeout_worker(self) -> None:
        await asyncio.sleep(self.timeout)
        await self.commit(by_timeout=True)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    # ──────────────────────────────────────────
    # 观测
    # ──────────────────────────────────────────
    def snapshot(self) -> str:
        """最终状态快照：余额 / 状态 / 是否仍持锁（仍持锁 = 被阻塞的实证）。"""
        lock = "仍持锁(被阻塞!)" if self.locked else "已释放"
        return f"{self.name}: balance={self.data['balance']}, state={self.state}, 锁={lock}"
