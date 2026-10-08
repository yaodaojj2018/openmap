"""等时圈路由：坐标 → 多级步行可达边界。

v1 为同步计算（~5 次批量矩阵调用，回放模式毫秒级）；任务化 + SSE 进度在 M3。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_provider
from app.core.config import Settings
from app.isochrone.engine import IsochroneError, compute_isochrone
from app.mapapi.provider import MapApiError, MapProvider
from app.models.common import Coord
from app.models.isochrone import IsochroneResult

router = APIRouter(tags=["isochrone"])


class IsochroneRequest(BaseModel):
    """POST /isochrone 请求体。"""

    origin: Coord = Field(description="中心点（crs 可选 bd09/gcj02/wgs84，内部统一转 bd09）")
    levels_min: list[int] = Field(
        default_factory=list,
        description="等时圈级别（分钟），缺省用服务端配置 [5, 10, 15]",
    )


@router.post("/isochrone", response_model=IsochroneResult, summary="多级步行等时圈（v1 同步）")
async def isochrone(
    request: Request,
    body: IsochroneRequest,
    provider: Annotated[MapProvider | None, Depends(get_provider)] = None,
) -> IsochroneResult:
    assert provider is not None  # FastAPI 依赖注入必定提供，仅为类型收窄
    settings: Settings = request.app.state.settings
    levels = body.levels_min or settings.isochrone_levels_min
    try:
        return (await compute_isochrone(provider, settings, body.origin.to_bd09(), levels)).result
    except IsochroneError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "BAD_LEVELS", "message": str(exc)}
        ) from exc
    except MapApiError as exc:
        # 上游异常不裸露给前端：统一 502 + 业务错误码（docs/02 §5.3）
        raise HTTPException(
            status_code=502,
            detail={"code": exc.kind.value, "message": "地图服务暂时不可用，请稍后重试"},
        ) from exc
