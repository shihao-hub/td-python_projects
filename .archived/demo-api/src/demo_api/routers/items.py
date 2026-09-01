import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from demo_api.db import get_db
from demo_api.models import Item
from demo_api.schemas import ItemCreate, ItemOut, ItemUpdate

router = APIRouter(prefix="/items", tags=["items"])
logger = structlog.get_logger(__name__)


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(payload: ItemCreate, db: AsyncSession = Depends(get_db)):
    item = Item(**payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    logger.info("item_created", item_id=item.id, title=item.title)
    return item


@router.get("", response_model=list[ItemOut])
async def list_items(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(Item).order_by(Item.id))
    return result.all()


@router.get("/{item_id}", response_model=ItemOut)
async def get_item(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@router.patch("/{item_id}", response_model=ItemOut)
async def update_item(item_id: int, payload: ItemUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    db.delete(item)
    await db.commit()
