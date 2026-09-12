"""五个场景：串行 vs 并发 / 阻塞事件循环 / TaskGroup / 超时取消 / 信号量与有界队列。"""

from __future__ import annotations

import asyncio
import time

from apps.common.lab import Timer, banner, check, conclude, fact, step


async def _fake_io(name: str, seconds: float = 1.0) -> str:
    """模拟一次网络/磁盘 IO：期间让出控制权。"""
    await asyncio.sleep(seconds)
    return f"{name}:done"


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：串行 vs 并发 —— await 的本质是"等待时让别人跑"
# ──────────────────────────────────────────────────────────────────────────

async def scenario_serial_vs_concurrent() -> None:
    banner("场景 1：10 个 1 秒的 IO —— 串行 vs gather vs TaskGroup")
    n = 10

    with Timer() as t_serial:
        results = []
        for i in range(n):
            results.append(await _fake_io(f"t{i}"))
    fact("串行 await", f"{t_serial.ms/1000:.1f}s")

    with Timer() as t_gather:
        results = await asyncio.gather(*[_fake_io(f"t{i}") for i in range(n)])
    fact("asyncio.gather", f"{t_gather.ms/1000:.1f}s")

    with Timer() as t_tg:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_fake_io(f"t{i}")) for i in range(n)]
    fact("TaskGroup", f"{t_tg.ms/1000:.1f}s")

    check(
        t_serial.ms > 9_000 and t_gather.ms < 2_000 and t_tg.ms < 2_000,
        "并发版总耗时约等于最慢的一个任务，而非所有任务之和",
        "耗时异常",
    )
    conclude(
        "await 不是「并行执行」，而是「等待时挂起自己、让事件循环调度别人」。"
        "串行 await 退化成同步代码；gather/TaskGroup 才真正铺开并发。"
        "TaskGroup（3.11+）是现代首选：子任务异常时自动取消兄弟任务，gather 不会。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：阻塞事件循环 —— time.sleep 是全场毒药
# ──────────────────────────────────────────────────────────────────────────

async def scenario_blocking_loop() -> None:
    banner("场景 2：阻塞调用冻结整个事件循环 —— 连无关的心跳都停摆")
    heartbeats: list[float] = []
    t0 = time.perf_counter()

    async def heartbeat() -> None:
        while True:
            heartbeats.append(time.perf_counter() - t0)
            await asyncio.sleep(0.2)

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.5)  # 先跑 2~3 拍

    step("反模式：在协程里 time.sleep(2)（模拟 requests/重 CPU）")
    time.sleep(2.0)  # 阻塞整个循环：heartbeat 也被冻结
    await asyncio.sleep(0.5)
    hb.cancel()

    gaps = [b - a for a, b in zip(heartbeats, heartbeats[1:])]
    max_gap = max(gaps)
    fact(f"心跳间隔序列", [f"{g:.2f}s" for g in gaps])
    fact("最大心跳间隔", f"{max_gap:.2f}s")
    check(max_gap > 1.5, "阻塞调用期间整个事件循环停摆：所有协程（包括无关的）一起冻结", "未复现")

    step("修复：asyncio.to_thread 把阻塞调用扔进线程池")
    heartbeats.clear()
    hb2 = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.5)
    await asyncio.to_thread(time.sleep, 2.0)  # 循环继续运转
    await asyncio.sleep(0.5)
    hb2.cancel()
    gaps2 = [b - a for a, b in zip(heartbeats, heartbeats[1:])]
    fact("修复后最大心跳间隔", f"{max(gaps2):.2f}s")
    check(max(gaps2) < 0.5, "循环不再停摆：阻塞工作在线程池里，事件循环照常调度", "修复无效")
    conclude(
        "事件循环是单线程的：任何一个协程阻塞（time.sleep、requests、同步 DB 驱动、重 CPU），"
        "所有协程全部停摆。FastAPI 里表现为整个进程的 P99 飙高。"
        "处方：async 生态库（httpx/asyncpg）+ asyncio.to_thread 兜底同步代码。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：TaskGroup —— 一个失败，兄弟全部取消，异常打包成组
# ──────────────────────────────────────────────────────────────────────────

async def scenario_taskgroup() -> None:
    banner("场景 3：TaskGroup 异常传播 —— 结构化并发")
    events: list[str] = []

    async def worker(name: str, fail: bool, lifetime: float) -> None:
        try:
            events.append(f"{name}:start")
            await asyncio.sleep(lifetime)
            if fail:
                raise ValueError(f"{name} 出错")
            events.append(f"{name}:done")
        except asyncio.CancelledError:
            events.append(f"{name}:cancelled")
            raise

    step("三个任务：slow(3s) / bad(1s 后抛错) / quick(0.5s)")
    with Timer() as t:
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(worker("slow", fail=False, lifetime=3.0))
                tg.create_task(worker("bad", fail=True, lifetime=1.0))
                tg.create_task(worker("quick", fail=False, lifetime=0.5))
        except* ValueError as eg:  # except* 按"异常组"过滤
            fact("捕获异常组", f"{[str(e) for e in eg.exceptions]}")
    fact("事件序列", events)
    fact("总耗时", f"{t.ms/1000:.1f}s（bad 1s 抛错后立即收场，没有等 slow 跑完 3s）")
    check("slow:cancelled" in events and t.ms < 2_500,
          "bad 失败 -> slow 被取消（结构化并发：不留孤儿任务）；quick 已完成不受影响", "取消语义异常")
    conclude(
        "TaskGroup = 结构化并发：任务组的作用域结束时，要么全部成功，要么异常向上冒泡"
        "且兄弟任务全被取消。对比 gather：默认等所有任务跑完才抛第一个异常，"
        "失败后兄弟还在空转。写新代码优先 TaskGroup。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：超时与取消 —— wait_for 触发 CancelledError，清理逻辑必须执行
# ──────────────────────────────────────────────────────────────────────────

async def scenario_timeout_cancel() -> None:
    banner("场景 4：wait_for 超时 —— 被取消的任务也要干净地退出")
    cleaned: list[str] = []

    async def job_with_resource() -> str:
        try:
            await asyncio.sleep(10)  # 模拟卡死的外部调用
            return "done"
        except asyncio.CancelledError:
            cleaned.append("resource-released")  # 清理放在 except CancelledError / finally
            raise                              # 必须 re-raise，否则任务"吞掉"取消

    with Timer() as t:
        try:
            await asyncio.wait_for(job_with_resource(), timeout=1.0)
        except asyncio.TimeoutError:
            fact("1s 超时触发", "TimeoutError")
    fact("清理动作", cleaned)
    fact("实际耗时", f"{t.ms/1000:.1f}s")
    check(cleaned == ["resource-released"] and t.ms < 1_500,
          "超时 -> 取消信号注入协程 -> finally/except 清理 -> 异常向外抛出", "清理未执行！")
    conclude(
        "wait_for 的取消是一条注入协程的 CancelledError：协程在下一个 await 点被唤醒。"
        "两个纪律：①资源清理写在 finally 或 except CancelledError 里；"
        "②捕获 CancelledError 后必须 re-raise，否则外层以为任务已正常结束。"
        "这就是'优雅停机'里 shutdown 信号的处理原型。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 5：并发上限与背压 —— Semaphore 与有界队列
# ──────────────────────────────────────────────────────────────────────────

async def scenario_semaphore_bounded() -> None:
    banner("场景 5：Semaphore 限并发 / 有界队列做背压")
    current = 0
    peak = 0

    async def limited_task(i: int) -> None:
        nonlocal current, peak
        async with semaphore:
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.1)
            current -= 1

    semaphore = asyncio.Semaphore(3)
    with Timer() as t:
        await asyncio.gather(*[limited_task(i) for i in range(10)])
    fact("10 个任务 / Semaphore(3)", f"峰值并发 {peak}，总耗时 {t.ms/1000:.1f}s（约 4 批）")
    check(peak == 3, "并发被精确压在 3：保护下游（DB 连接池/第三方配额）不被打爆", "限流失效")

    step("有界队列：生产快、消费慢 -> 生产者被 put 背压")
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=5)
    produced_log: list[float] = []
    t0 = time.perf_counter()

    async def producer() -> None:
        for i in range(12):
            await q.put(i)  # 队列满时阻塞：背压向上游传播
            produced_log.append(time.perf_counter() - t0)

    async def consumer() -> None:
        for _ in range(12):
            await q.get()
            await asyncio.sleep(0.05)  # 消费慢于生产

    await asyncio.gather(producer(), consumer())
    fact("12 件产出时刻(s)", [f"{x:.2f}" for x in produced_log[5:]])  # 展示第 6 件之后才开始受限
    check(produced_log[-1] > 0.25, "消费者不领走，生产者 put 就挂起：内存不会被无限撑大", "背压未生效")
    conclude(
        "并发控制两件套：Semaphore 限制'同时在跑'的数量（保护下游）；"
        "有界队列在'生产消费速率不匹配'时把压力反推给生产者（保护内存）。"
        "无界队列 = 把内存当无限资源，是流水线系统的隐形炸弹。"
    )


SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("serial-vs-concurrent", scenario_serial_vs_concurrent, "串行 vs gather vs TaskGroup")
_reg("blocking-loop", scenario_blocking_loop, "time.sleep 冻结整个循环；to_thread 修复")
_reg("taskgroup", scenario_taskgroup, "TaskGroup：一个失败兄弟全取消（结构化并发）")
_reg("timeout-cancel", scenario_timeout_cancel, "wait_for 超时取消与资源清理")
_reg("semaphore-bounded", scenario_semaphore_bounded, "Semaphore 限并发；有界队列背压")


async def run_all() -> None:
    for fn in SCENARIOS.values():
        await fn()
