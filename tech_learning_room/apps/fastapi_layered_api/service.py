"""Service 层：业务规则与事务编排。跨仓储写入在这里获得原子性。"""

from __future__ import annotations

from apps.fastapi_layered_api.models import CollectionItemRepository, CollectionRepository


class ConflictError(RuntimeError):
    """业务冲突：同名收藏夹已存在。"""


class ServiceError(RuntimeError):
    """业务失败：演示跨仓储事务整体回滚。"""


class CollectionService:
    """一个请求一个事务：方法内部 commit/rollback，Repository 永远只 flush。"""

    def __init__(self, session) -> None:
        self.session = session
        self.collections = CollectionRepository(session)
        self.items = CollectionItemRepository(session)

    async def create_collection(self, name: str, description: str, item_titles: list[str]) -> dict:
        try:
            existing = await self.collections.find_by_name(name)
            if existing is not None:
                raise ConflictError(f"collection name duplicated: {name}")

            row = await self.collections.create(name, description)
            count = 0
            if item_titles:
                count = await self.items.bulk_create(row.id, item_titles)
            await self.session.commit()
            return {"id": row.id, "name": name, "items": count}
        except ConflictError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise

    async def create_with_injected_failure(
        self, name: str, description: str, item_titles: list[str]
    ) -> dict:
        """演示：collection + items 写入后业务失败 -> 两个表一起回滚。"""
        try:
            row = await self.collections.create(name, description)
            if item_titles:
                await self.items.bulk_create(row.id, item_titles)
            raise ServiceError("模拟条目落库后业务校验失败：collection 与 items 必须一起消失")
        except Exception:
            await self.session.rollback()
            raise

    async def list_collections(self, limit: int = 20, offset: int = 0) -> dict:
        rows, total = await self.collections.list_all(limit=limit, offset=offset)
        await self.session.commit()
        return {
            "items": [{"id": r.id, "name": r.name, "description": r.description} for r in rows],
            "total": total,
        }

    async def get_detail(self, collection_id: int) -> dict | None:
        row = await self.collections.get(collection_id)
        if row is None:
            return None
        items = await self.items.list_by_collection(collection_id)
        await self.session.commit()
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "items": [{"seq": i.seq, "title": i.title} for i in items],
        }

    async def delete_collection(self, collection_id: int) -> bool:
        row = await self.collections.get(collection_id)
        if row is None:
            return False
        await self.collections.delete(row)  # 级联删除由 FK ON DELETE CASCADE 承担
        await self.session.commit()
        return True
