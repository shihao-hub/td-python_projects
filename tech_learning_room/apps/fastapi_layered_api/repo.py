"""Repository 层：SQL 的唯一归宿。只 flush 不 commit，显式列出全部字段。"""

from __future__ import annotations

from sqlalchemy import func, select

from apps.fastapi_layered_api.models import CollectionItemRow, CollectionRow


class CollectionRepository:
    def __init__(self, session) -> None:
        self.session = session

    async def find_by_name(self, name: str) -> CollectionRow | None:
        return (
            await self.session.execute(
                select(CollectionRow).where(CollectionRow.name == name)
            )
        ).scalar_one_or_none()

    async def create(self, name: str, description: str) -> CollectionRow:
        row = CollectionRow(name=name, description=description)
        self.session.add(row)
        await self.session.flush()  # 拿 id；事务成败交给 Service
        return row

    async def list_all(self, limit: int = 20, offset: int = 0) -> tuple[list, int]:
        rows = (
            (
                await self.session.execute(
                    select(CollectionRow.id, CollectionRow.name, CollectionRow.description)
                    .order_by(CollectionRow.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .fetchall()
        )
        total = await self.session.scalar(select(func.count()).select_from(CollectionRow))
        return rows, int(total)

    async def get(self, collection_id: int) -> CollectionRow | None:
        return await self.session.get(CollectionRow, collection_id)

    async def delete(self, row: CollectionRow) -> None:
        await self.session.delete(row)
        await self.session.flush()


class CollectionItemRepository:
    def __init__(self, session) -> None:
        self.session = session

    async def bulk_create(self, collection_id: int, titles: list[str]) -> int:
        for seq, title in enumerate(titles, start=1):
            self.session.add(
                CollectionItemRow(collection_id=collection_id, seq=seq, title=title)
            )
        await self.session.flush()
        return len(titles)

    async def list_by_collection(self, collection_id: int) -> list:
        return (
            (
                await self.session.execute(
                    select(
                        CollectionItemRow.id, CollectionItemRow.seq, CollectionItemRow.title
                    )
                    .where(CollectionItemRow.collection_id == collection_id)
                    .order_by(CollectionItemRow.seq)
                )
            )
            .fetchall()
        )
