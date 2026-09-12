"""四个场景：连接池耗尽 / 短事务+DTO / Detached 实例 / 工作单元。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select, update

from apps.common.lab import Timer, banner, check, conclude, fact, step
from apps.sqlalchemy_session_lifecycle.db import AuditRow, Session, TaskRow, pool_status, reset_schema


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：长事务耗尽连接池 —— "等待外部 IO"期间连接被白白占着
# ──────────────────────────────────────────────────────────────────────────

async def _long_txn_task(task_id: int, hold_seconds: float) -> str:
    """反模式：一个 Session 从头用到尾，中间还夹着外部 IO 等待。"""
    async with Session() as session:
        row = await session.get(TaskRow, task_id)
        await asyncio.sleep(hold_seconds)  # 模拟调用外部 API：连接全程被占用
        row.status = "done"
        row.result_text = f"processed by long-txn after {hold_seconds}s"
        await session.commit()
        return f"task-{task_id} done"


async def scenario_pool_exhausted() -> None:
    banner("场景 1：长事务耗尽连接池 —— pool_size=2 时第三个请求直接超时")
    await reset_schema()
    step(f"初始池状态: {pool_status()}")

    async def guarded(task_id: int) -> tuple[int, str]:
        try:
            return task_id, await _long_txn_task(task_id, 1.5)
        except Exception as exc:  # noqa: BLE001
            return task_id, f"!! {exc.__class__.__name__}: {exc}"

    step("并发 3 个任务，每个都'拿着连接睡 1.5 秒'...")
    with Timer() as t:
        results = await asyncio.gather(guarded(1), guarded(2), guarded(3))
    for task_id, msg in results:
        fact(f"task-{task_id}", msg)
    step(f"结束时池状态: {pool_status()}")
    failed = [r for r in results if r[1].startswith("!!")]
    check(len(failed) == 1 and "TimeoutError" in failed[0][1],
          "第三个任务在 pool_timeout=2s 后拿到 TimeoutError：对外表现为接口批量超时",
          "未复现（时序或池参数异常）")
    conclude(
        "连接池的容量是'同时在途事务数'的上限。把外部 IO（HTTP 调用、sleep、等用户输入）"
        "包进事务里，等于用最贵的资源（连接）等最慢的事情。"
        "故障特征极具迷惑性：报错的是无辜的第三个请求，真正的元凶是前两个长事务。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：短事务 + DTO 快照 —— 等待期间把连接还回去
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskSnapshot:
    """DTO 快照：离开 Session 的数据只能是纯数据，不能是 ORM 对象。"""

    id: int
    name: str
    status: str


async def _short_txn_task(task_id: int, wait_seconds: float) -> str:
    """正确姿势：两个短事务夹一段'不占连接'的等待。"""
    # 短事务 1：读快照并立刻归还连接
    async with Session() as session:
        row = await session.get(TaskRow, task_id)
        snap = TaskSnapshot(id=row.id, name=row.name, status=row.status)
    print(f"      [task-{task_id}] 读完快照，池状态: {pool_status()}")

    await asyncio.sleep(wait_seconds)  # 等外部 API —— 此刻不占任何连接

    # 短事务 2：写结果
    async with Session() as session:
        await session.execute(
            update(TaskRow)
            .where(TaskRow.id == snap.id)
            .values(status="done", result_text=f"processed by short-txn after {wait_seconds}s")
        )
        await session.commit()
    return f"task-{task_id} done"


async def scenario_short_txn_dto() -> None:
    banner("场景 2：短事务 + DTO —— 同样的并发量，连接池毫无压力")
    await reset_schema()
    step(f"初始池状态: {pool_status()}")
    results = await asyncio.gather(*[_short_txn_task(i, 1.5) for i in range(1, 6)])
    for msg in results:
        fact("结果", msg)
    rows = (await Session().execute(select(TaskRow.id, TaskRow.status).order_by(TaskRow.id))).fetchall()
    fact("最终状态", rows)
    check(all(status == "done" for _, status in rows), "5 个任务全部完成：等待期间连接已归还给其他任务复用", "存在未完成任务")
    conclude(
        "模式：BEGIN -> 读快照 -> COMMIT -> (无连接等待) -> BEGIN -> 写结果 -> COMMIT。"
        "'等外部 IO'与'持有事务'彻底解耦。代价是两次事务之间数据可能被别人改过——"
        "所以 UPDATE 用快照里的业务键定位而不是乐观地认为行没变；需要更强保证时用版本号 CAS。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：Detached 实例 —— commit 后的 ORM 对象还能用吗
# ──────────────────────────────────────────────────────────────────────────

async def scenario_detached_instance() -> None:
    banner("场景 3：expire_on_commit 与 DetachedInstanceError")
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from apps.sqlalchemy_session_lifecycle.db import engine

    step("3.1 默认 expire_on_commit=True：commit 后对象属性过期，需要重新加载")
    exp_session_factory = async_sessionmaker(engine, expire_on_commit=True)
    session = exp_session_factory()
    try:
        task = TaskRow(name="demo", status="pending")
        session.add(task)
        await session.commit()
        step(f"commit 后立刻访问（session 还开着）: name = {task.name!r}   <- 触发了一次隐式 SELECT 刷新")
    finally:
        await session.close()

    step("3.2 同一个对象在 session.close() 之后再访问：")
    try:
        print(f"      name = {task.name!r}")
        check(False, "竟然成功了？", "不应发生")
    except Exception as exc:  # noqa: BLE001
        check(type(exc).__name__ == "DetachedInstanceError",
              f"{type(exc).__name__}：对象已脱离会话，属性处于过期状态无法加载", f"异常类型异常: {exc!r}")

    step("3.3 expire_on_commit=False + 显式 DTO：跨会话传数据的正解")
    session2 = Session()  # 项目默认工厂即 expire_on_commit=False
    try:
        task2 = TaskRow(name="demo2", status="pending")
        session2.add(task2)
        await session2.commit()
        dto = {"id": task2.id, "name": task2.name}  # 在会话内转成纯数据
    finally:
        await session2.close()
    fact("会话关闭后使用 DTO", dto)
    check(dto["name"] == "demo2", "DTO 是纯数据，与会话生命周期无关", "DTO 转换异常")
    conclude(
        "expire_on_commit=True（默认）在 commit 时把所有属性标记过期，下次访问隐式发 SELECT——"
        "既是 N+1 的隐形来源，也在对象离开会话后直接抛 DetachedInstanceError。"
        "约定：①异步项目常设 False 消除隐式刷新；②无论哪种设置，跨层/跨会话只传 DTO 或值对象；"
        "③Celery 任务里尤其如此——任务函数返回前把 ORM 对象转 dict，别让对象活着穿过进程边界。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：工作单元（UoW）—— Repository 只 flush，事务成败归 Service
# ──────────────────────────────────────────────────────────────────────────

class TaskRepository:
    """仓储：只做存取与 flush，绝不 commit。"""

    def __init__(self, session) -> None:
        self.session = session

    async def create_task(self, name: str) -> int:
        task = TaskRow(name=name, status="pending")
        self.session.add(task)
        await self.session.flush()  # flush 拿 id，但事务未定
        return task.id

    async def write_audit(self, task_id: int, action: str) -> None:
        self.session.add(AuditRow(task_id=task_id, action=action))


class TaskService:
    """服务：编排多个仓储写入，统一决定 commit / rollback。"""

    def __init__(self, session) -> None:
        self.session = session
        self.tasks = TaskRepository(session)

    async def create_with_audit(self, name: str, *, fail: bool) -> int:
        task_id = await self.tasks.create_task(name)
        await self.tasks.write_audit(task_id, f"created task {name}")
        if fail:
            raise RuntimeError("业务校验失败：模拟事务必须整体回滚")
        await self.session.commit()
        return task_id


async def scenario_unit_of_work() -> None:
    banner("场景 4：工作单元 —— 跨仓储的写入要么全成、要么全无")
    from sqlalchemy import func, select

    from apps.sqlalchemy_session_lifecycle.db import AuditRow

    await reset_schema()

    step("4.1 失败路径：audit 写完但业务校验失败")
    session = Session()
    try:
        await TaskService(session).create_with_audit("should-rollback", fail=True)
    except RuntimeError as exc:
        await session.rollback()
        fact("捕获", exc)
    finally:
        await session.close()

    step("4.2 成功路径：task + audit 同事务提交")
    session = Session()
    try:
        ok_id = await TaskService(session).create_with_audit("should-commit", fail=False)
        fact("新任务 id", ok_id)
    finally:
        await session.close()

    async with Session() as s:
        task_count = await s.scalar(select(func.count()).select_from(TaskRow))
        audit_count = await s.scalar(select(func.count()).select_from(AuditRow))
        rolled = await s.scalar(select(func.count()).select_from(TaskRow).where(TaskRow.name == "should-rollback"))
    fact("tasks 总数", task_count)
    fact("audit 总数", audit_count)
    fact("should-rollback 残留", rolled)
    check(task_count == 5 and audit_count == 1 and rolled == 0,
          "失败事务零残留：task 与 audit 一起消失；成功事务两者同时落库", "事务边界泄漏！")
    conclude(
        "规则：Repository 只 flush 不 commit；Service 编排并决定事务成败；"
        "（若用 FastAPI，Router/依赖注入层持有 session 生命周期）。"
        "这样'一个请求 = 一个事务'的边界清晰可测，跨仓储写入天然原子。"
    )


SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("pool-exhausted", scenario_pool_exhausted, "长事务占满连接池 -> 第三个请求超时")
_reg("short-txn-dto", scenario_short_txn_dto, "短事务+DTO：等待期间归还连接")
_reg("detached-instance", scenario_detached_instance, "expire_on_commit 与 DetachedInstanceError")
_reg("unit-of-work", scenario_unit_of_work, "工作单元：Repository flush、Service 决定成败")


async def run_all() -> None:
    from apps.sqlalchemy_session_lifecycle.db import engine

    try:
        for fn in SCENARIOS.values():
            await fn()
    finally:
        await engine.dispose()
