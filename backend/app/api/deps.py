"""路由层依赖注入：从 app.state 取 Provider / 缓存（lifespan 中装配）。"""

from __future__ import annotations

from fastapi import Request

from app.core.cache import CacheBackend
from app.mapapi.provider import MapProvider


def get_provider(request: Request) -> MapProvider:
    return request.app.state.provider


def get_cache(request: Request) -> CacheBackend:
    return request.app.state.cache
