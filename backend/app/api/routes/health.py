"""健康检查：暴露 Provider 模式与缓存模式，供 compose healthcheck 与巡检。"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "provider": request.app.state.provider.name,
        "cache": type(request.app.state.cache).__name__,
        "demo_mode": settings.demo_mode,
    }
