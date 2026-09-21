"""应用入口：lifespan 装配（日志/缓存/Provider）+ 路由注册。

启动日志输出脱敏配置摘要（CLAUDE.md 铁律 #6：密钥永不入日志）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import demo, geocode, health, poi
from app.core.cache import build_cache
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.mapapi import build_provider


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # create_app 可注入测试用 Settings；未注入时走全局配置（含 .env/环境变量）
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    configure_logging(settings.debug)
    log = structlog.get_logger(__name__)
    log.info("app.starting", **settings.effective_summary())  # type: ignore[arg-type]

    app.state.settings = settings
    app.state.cache = await build_cache(settings)
    app.state.provider = build_provider(settings, app.state.cache)

    if not settings.demo_mode and not settings.baidu_ak:
        log.warning("app.missing_ak_fallback_replay")
    yield
    await app.state.provider.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    app = FastAPI(
        title="OpenMap · 15 分钟生活圈智能体检助手",
        version=resolved.app_version,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (health.router, geocode.router, poi.router, demo.router):
        app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
