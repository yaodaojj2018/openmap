"""POI 检索路由：分类目圆形区域检索（含清洗去重，返回 bd09）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.api.deps import get_provider
from app.core.config import Settings
from app.mapapi.provider import MapApiError, MapProvider
from app.models.common import Coord, Crs
from app.models.poi import CategoryKey, PoiSearchResult
from app.poi.service import load_taxonomy, search_categories

router = APIRouter(tags=["poi"])


@router.get("/pois", response_model=PoiSearchResult, summary="分类 POI 检索")
async def pois(
    request: Request,
    lng: Annotated[float, Query(description="中心点经度")],
    lat: Annotated[float, Query(description="中心点纬度")],
    crs: Annotated[Crs, Query(description="输入坐标系，内部统一转 bd09")] = "bd09",
    categories: Annotated[
        str | None, Query(description="逗号分隔类目，缺省全部：medical,education,shopping,elderly")
    ] = None,
    radius: Annotated[int | None, Query(ge=100, le=5000, description="检索半径（米）")] = None,
    provider: Annotated[MapProvider | None, Depends(get_provider)] = None,
) -> PoiSearchResult:
    assert provider is not None  # FastAPI 依赖注入必定提供，仅为类型收窄
    settings: Settings = request.app.state.settings
    try:
        coord = Coord(lng=lng, lat=lat, crs=crs)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "BAD_COORD", "message": str(exc)}
        ) from exc
    center = coord.to_bd09()

    taxonomy = load_taxonomy(settings.poi_taxonomy_file)
    valid_keys = [spec.key for spec in taxonomy]
    keys: list[CategoryKey] = valid_keys
    if categories:
        requested = [c.strip() for c in categories.split(",") if c.strip()]
        unknown = [c for c in requested if c not in valid_keys]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "BAD_CATEGORY",
                    "message": f"未知类目: {unknown}，可选: {valid_keys}",
                },
            )
        keys = [c for c in requested if c in valid_keys]  # type: ignore[misc]

    radius_m = radius or settings.poi_search_radius_m
    try:
        result = await search_categories(provider, settings, taxonomy, center, keys, radius_m)
    except MapApiError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.kind.value, "message": "地图服务暂时不可用，请稍后重试"},
        ) from exc

    return PoiSearchResult(
        origin=Coord(lng=center[0], lat=center[1], crs="bd09"), radius_m=radius_m, categories=result
    )
