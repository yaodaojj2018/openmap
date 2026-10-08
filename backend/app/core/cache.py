"""两级缓存抽象：进程内 LRU（L1）+ Redis（L2，可选）。

- Redis 不可用或未配置时自动退化为纯内存缓存（开发/CI 友好）；
- Redis 在运行中失联时 L2 读写降级为 L1 直通（缓存是优化，永不作正确性依赖）；
- Key 由调用方规范化生成（cache_key：参数排序 + 紧凑 JSON + MD5）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Protocol

from app.core.config import Settings


def cache_key(namespace: str, **params: object) -> str:
    """规范化缓存键，保证相同语义参数命中同一键。"""
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_s: int) -> None: ...


class MemoryCache:
    """进程内 TTL 缓存（LRU 淘汰），容量 4096。"""

    def __init__(self, capacity: int = 4096) -> None:
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._capacity = capacity

    async def get(self, key: str) -> str | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if expires_at < time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        self._data[key] = (time.monotonic() + ttl_s, value)
        self._data.move_to_end(key)
        while len(self._data) > self._capacity:
            self._data.popitem(last=False)


class RedisCache:
    """Redis 异步客户端封装，连接惰性建立。"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None

    def _ensure(self):
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._url, decode_responses=True)
        return self._client

    async def get(self, key: str) -> str | None:
        return await self._ensure().get(key)

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        await self._ensure().set(key, value, ex=ttl_s)


async def build_cache(settings: Settings) -> CacheBackend:
    """按配置选择缓存后端；Redis 连不通时降级内存并记录日志。"""
    import structlog

    log = structlog.get_logger(__name__)
    if settings.redis_url:
        redis_cache = RedisCache(settings.redis_url)
        try:
            await redis_cache._ensure().ping()
            log.info("cache.redis_connected")
            return redis_cache
        except Exception as exc:  # 任何连接问题都降级为内存缓存
            log.warning("cache.redis_unavailable_fallback_memory", error=str(exc))
    return MemoryCache()
