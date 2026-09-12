"""Router 层：只做编排——解析请求、调用 Service、包装 CommonResult。

铁律：Router 不写 SQL、不碰业务规则；
Pydantic 模型是接口契约，异常处理器把业务异常翻译成统一信封。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.fastapi_layered_api.models import Session
from apps.fastapi_layered_api.service import CollectionService, ConflictError, ServiceError

router = APIRouter(prefix="/api/v1/collections", tags=["collections"])

T = TypeVar("T")


class CommonResult(BaseModel, Generic[T]):
    """统一响应信封：前端只认这一种形状。"""

    code: int = 200
    message: str = "success"
    data: T | None = None
    success: bool = True

    @classmethod
    def ok(cls, data: T | None = None) -> "CommonResult[T]":
        return cls(code=200, message="success", data=data, success=True)

    @classmethod
    def error(cls, code: int, message: str) -> "CommonResult[None]":
        return cls(code=code, message=message, data=None, success=False)


# ── 请求/响应契约 ────────────────────────────────────────────────────────

class CollectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="收藏夹名，唯一")
    description: str = Field(default="", max_length=256)
    item_titles: list[str] = Field(default_factory=list, description="初始条目")


class CollectionSummary(BaseModel):
    id: int
    name: str
    items: int


# ── 依赖注入 ─────────────────────────────────────────────────────────────

async def get_session() -> AsyncSession:
    async with Session() as session:
        yield session


def get_collection_service(session: AsyncSession = Depends(get_session)) -> CollectionService:
    return CollectionService(session)


# ── 路由 ─────────────────────────────────────────────────────────────────

@router.post("", response_model=CommonResult[CollectionSummary])
async def create_collection(
    req: CollectionCreateRequest,
    service: CollectionService = Depends(get_collection_service),
) -> CommonResult[CollectionSummary]:
    try:
        result = await service.create_collection(req.name, req.description, req.item_titles)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CommonResult.ok(CollectionSummary(**result))


@router.post("/rollback-demo", response_model=CommonResult[None])
async def create_with_injected_failure(
    req: CollectionCreateRequest,
    service: CollectionService = Depends(get_collection_service),
) -> CommonResult[None]:
    """教学专用：写入后必定失败，用于观察跨仓储回滚。"""
    try:
        await service.create_with_injected_failure(req.name, req.description, req.item_titles)
    except ServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CommonResult.ok()


@router.get("", response_model=CommonResult[dict])
async def list_collections(
    service: CollectionService = Depends(get_collection_service),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> CommonResult[dict]:
    data = await service.list_collections(limit=size, offset=(page - 1) * size)
    return CommonResult.ok(data)


@router.get("/{collection_id}", response_model=CommonResult[dict])
async def get_collection(
    collection_id: int,
    service: CollectionService = Depends(get_collection_service),
) -> CommonResult[dict]:
    data = await service.get_detail(collection_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"collection {collection_id} not found")
    return CommonResult.ok(data)


@router.delete("/{collection_id}", response_model=CommonResult[None])
async def delete_collection(
    collection_id: int,
    service: CollectionService = Depends(get_collection_service),
) -> CommonResult[None]:
    deleted = await service.delete_collection(collection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"collection {collection_id} not found")
    return CommonResult.ok()
