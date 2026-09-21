"""地理编码路由：地址 → BD09 坐标候选。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_provider
from app.mapapi.provider import MapApiError, MapProvider
from app.models.geocode import GeocodeResult

router = APIRouter(tags=["geocode"])


@router.get("/geocode", response_model=GeocodeResult, summary="地址转坐标（bd09）")
async def geocode(
    q: Annotated[str, Query(min_length=2, max_length=100, description="地址或地名")],
    city: Annotated[str | None, Query(max_length=30, description="城市约束，消除同名歧义")] = None,
    provider: Annotated[MapProvider | None, Depends(get_provider)] = None,
) -> GeocodeResult:
    assert provider is not None  # FastAPI 依赖注入必定提供，仅为类型收窄
    try:
        candidates = await provider.geocode(q, city)
    except MapApiError as exc:
        # 上游异常不裸露给前端：统一 502 + 业务错误码（docs/02 §5.3）
        raise HTTPException(
            status_code=502,
            detail={"code": exc.kind.value, "message": "地图服务暂时不可用，请稍后重试"},
        ) from exc
    return GeocodeResult(candidates=candidates)
