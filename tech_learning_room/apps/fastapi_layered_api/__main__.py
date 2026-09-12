"""入口：

    uv run python -m apps.fastapi_layered_api --serve --port 8710   # 启动 API 服务
    uv run python -m apps.fastapi_layered_api                        # 自动起服务 + 跑演示
    uv run python -m apps.fastapi_layered_api --list
"""

from __future__ import annotations

import argparse
import asyncio
import threading
import time

import httpx
import uvicorn

from apps.common.lab import banner, check, conclude, fact, step
from apps.common.lab import Timer


def _build_server(port: int) -> uvicorn.Server:
    config = uvicorn.Config(
        "apps.fastapi_layered_api.app:app", host="127.0.0.1", port=port, log_level="warning"
    )
    return uvicorn.Server(config)


def _run_server_in_thread(server: uvicorn.Server) -> threading.Thread:
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    return th


async def _demo(port: int) -> None:
    from apps.fastapi_layered_api.models import Session, reset_schema
    from sqlalchemy import text

    step("重置 schema（tlr_api）...")
    await reset_schema()

    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(base_url=base, timeout=10) as client:
        banner("演示 1：创建收藏夹（含初始条目）—— Router -> Service -> Repository")
        resp = await client.post(
            "/api/v1/collections",
            json={"name": "spring-drop", "description": "春季选品", "item_titles": ["帽子", "衬衫", "帆布鞋"]},
        )
        fact("POST /collections", f"{resp.status_code} {resp.json()}")
        ok1 = resp.status_code == 200 and resp.json()["data"]["items"] == 3

        banner("演示 2：Pydantic 契约校验 —— name 为空 -> 422")
        resp = await client.post("/api/v1/collections", json={"name": ""})
        fact("POST（空 name）", f"{resp.status_code}（校验在进入 Service 之前完成）")
        ok2 = resp.status_code == 422

        banner("演示 3：业务冲突 —— 同名收藏夹 -> 409 统一信封")
        resp = await client.post("/api/v1/collections", json={"name": "spring-drop"})
        fact("POST（重名）", f"{resp.status_code} {resp.json()['message']}")
        ok3 = resp.status_code == 409 and resp.json()["success"] is False

        banner("演示 4：列表 / 详情 / 删除")
        resp = await client.get("/api/v1/collections")
        fact("GET 列表", f"{resp.status_code} total={resp.json()['data']['total']}")
        cid = resp.json()["data"]["items"][0]["id"]
        resp = await client.get(f"/api/v1/collections/{cid}")
        fact("GET 详情", f"{resp.status_code} items={[i['title'] for i in resp.json()['data']['items']]}")
        resp = await client.delete(f"/api/v1/collections/{cid}")
        fact("DELETE", resp.status_code)
        resp = await client.get(f"/api/v1/collections/{cid}")
        fact("删除后 GET", resp.status_code)

        banner("演示 5：跨仓储事务回滚 —— collection + items 写入后失败")
        resp = await client.post(
            "/api/v1/collections/rollback-demo",
            json={"name": "ghost", "item_titles": ["a", "b"]},
        )
        fact("POST /rollback-demo", f"{resp.status_code}（业务注入失败）")
        async with Session() as session:
            n_coll = await session.execute(text("SELECT COUNT(*) FROM tlr_api.collections"))
            n_item = await session.execute(text("SELECT COUNT(*) FROM tlr_api.collection_items"))
            coll_count, item_count = n_coll.fetchone()[0], n_item.fetchone()[0]
        fact("collections 行数", coll_count)
        fact("collection_items 行数", item_count)
        ok5 = resp.status_code == 500 and coll_count == 0 and item_count == 0

    check(ok1 and ok2 and ok3 and ok5,
          "全部演示通过：校验前置、冲突 409、统一信封、跨仓储写入整体回滚", "存在失败演示")
    conclude(
        "分层铁律的收益：Router 换成 MCP/GraphQL 时 Service 原封不动；"
        "Repository 是 SQL 唯一归宿，审查只看一处；事务边界收敛在 Service，"
        "跨仓储写入天然原子。换一个 HTTP 框架的成本被压缩到 router.py 一个文件。"
    )


async def main_async(args: argparse.Namespace) -> None:
    if args.list:
        print("fastapi_layered_api：无独立场景，用 demo（默认）或 --serve 启动服务后自行 curl。")
        print("  演示内容：创建 / 422 校验 / 409 冲突 / 列表详情删除 / 跨仓储事务回滚")
        return

    port = args.port
    server = _build_server(port)
    th = _run_server_in_thread(server)
    for _ in range(50):  # 等服务就绪
        if server.started:
            break
        time.sleep(0.1)

    if args.serve:
        step(f"API 服务运行中: http://127.0.0.1:{port}/docs （Ctrl+C 退出）")
        try:
            while th.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            server.should_exit = True
    else:
        try:
            await _demo(port)
        finally:
            server.should_exit = True
            th.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(prog="fastapi_layered_api", description="分层 API 实验")
    parser.add_argument("--list", action="store_true", help="查看说明")
    parser.add_argument("--serve", action="store_true", help="启动 API 服务（不跑演示）")
    parser.add_argument("--port", type=int, default=8710, help="监听端口（默认 8710）")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
