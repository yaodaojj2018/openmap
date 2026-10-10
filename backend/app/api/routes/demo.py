"""演示模式路由：示例社区提示（前端"加载示例"按钮数据源）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_provider
from app.mapapi.provider import MapProvider
from app.mapapi.replay.client import ReplayProvider

router = APIRouter(tags=["demo"])

# 回放模式之外也返回固定示例，保证入口一致（真实模式下将真实调用 API）
DEFAULT_HINT: dict[str, object] = {
    "address": "北京市海淀区中关村大街1号",
    "origin": {"lng": 116.316628, "lat": 39.981909, "crs": "bd09"},
    "entry_points": [
        {"lng": 116.315628, "lat": 39.981909, "crs": "bd09"},
        {"lng": 116.317628, "lat": 39.981909, "crs": "bd09"},
    ],
    "categories": ["medical", "education", "shopping", "elderly"],
}


@router.get("/demo/hint", summary="示例社区提示")
async def demo_hint(
    request: Request,
    provider: Annotated[MapProvider, Depends(get_provider)],
) -> dict[str, object]:
    if isinstance(provider, ReplayProvider) and provider.meta:
        return provider.meta
    return DEFAULT_HINT
