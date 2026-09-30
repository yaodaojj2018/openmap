"""百度地图 Web 服务 API 适配器。

限流/重试/缓存/SN 签名全部收口在此（CLAUDE.md 铁律 #3）：
- 令牌桶按配置限速，排队不丢弃；
- 仅对 TIMEOUT / SERVER / RATE_LIMIT 指数退避重试；
- 成功响应整体缓存（key 由参数规范化生成）；
- status 码分类默认表以官方文档常见口径为准，可用 OPENMAP_STATUS_KIND_OVERRIDES 覆盖。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.cache import CacheBackend, MemoryCache, cache_key
from app.core.config import Settings
from app.core.ratelimit import TokenBucket
from app.mapapi.provider import (
    RETRYABLE_KINDS,
    BD09Point,
    ErrorKind,
    MapApiError,
)
from app.models.geocode import GeocodeCandidate
from app.models.isochrone import RouteLeg
from app.models.poi import PoiRecord

_BASE_URL = "https://api.map.baidu.com"

# 百度 status 码 → 错误类别（默认口径，可被 settings.status_kind_overrides 覆盖）。
# 仅收录确定性较高的条目；未收录状态码统一归 UNKNOWN（不可重试），避免误重试放大故障。
_DEFAULT_STATUS_KIND: dict[int, ErrorKind] = {
    1: ErrorKind.SERVER,
    2: ErrorKind.BAD_REQUEST,
    3: ErrorKind.AUTH,
    4: ErrorKind.QUOTA,
    5: ErrorKind.AUTH,
    210: ErrorKind.AUTH,
}


class BaiduClient:
    name = "baidu"

    def __init__(self, settings: Settings, cache: CacheBackend | None = None) -> None:
        self._s = settings
        self._log = structlog.get_logger(__name__)
        self._cache = cache or MemoryCache()
        self._bucket = TokenBucket(settings.api_qps, settings.api_burst)
        self._client = httpx.AsyncClient(base_url=_BASE_URL, timeout=settings.http_timeout_s)
        overrides = {int(k): ErrorKind(v) for k, v in settings.status_kind_overrides.items()}
        self._status_kind = _DEFAULT_STATUS_KIND | overrides

    # ---- 基础请求 ----

    def _calc_sn(self, path: str, params: dict[str, str]) -> str:
        """SN 签名：sn = MD5(urlencode(path + "?" + query, safe="/?=&%"))，官方文档口径。"""
        query = "&".join(f"{k}={v}" for k, v in params.items())
        raw = quote(f"{path}?{query}", safe="/?=&%")
        return hashlib.md5((raw + self._s.baidu_sk).encode()).hexdigest()

    async def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """单次请求：限流 → 缓存 → HTTP → status 分类。返回已缓存或新响应 JSON。"""
        request_params = {**params, "output": "json", "ak": self._s.baidu_ak}
        if self._s.baidu_sk:
            request_params["sn"] = self._calc_sn(path, request_params)

        key = cache_key("baidu", path=path, **params)
        cached = await self._cache.get(key)
        if cached is not None:
            return json.loads(cached)

        await self._bucket.acquire()
        try:
            resp = await self._client.get(path, params=request_params)
        except httpx.TimeoutException as exc:
            raise MapApiError(ErrorKind.TIMEOUT, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise MapApiError(ErrorKind.SERVER, str(exc)) from exc

        if resp.status_code >= 500:
            raise MapApiError(ErrorKind.SERVER, f"http {resp.status_code}")
        if resp.status_code != 200:
            raise MapApiError(ErrorKind.BAD_REQUEST, f"http {resp.status_code}")

        data = resp.json()
        status = data.get("status", 0)
        if status != 0:
            kind = self._status_kind.get(int(status), ErrorKind.UNKNOWN)
            raise MapApiError(kind, data.get("message", "unknown baidu error"), status=status)

        await self._cache.set(key, resp.text, self._s.cache_ttl_s)
        return data

    async def request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """带重试的请求入口：仅可重试类别触发指数退避。"""

        def _is_retryable(exc: BaseException) -> bool:
            return isinstance(exc, MapApiError) and exc.kind in RETRYABLE_KINDS

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._s.retry_max_attempts),
            wait=wait_exponential(multiplier=self._s.retry_backoff_s),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                return await self._request(path, params)
        raise MapApiError(ErrorKind.UNKNOWN, "unreachable")  # pragma: no cover

    # ---- 协议实现 ----

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        params = {"address": address}
        if city:
            params["city"] = city
        data = await self.request("/geocoding/v3/", params)
        result = data.get("result") or {}
        location = result.get("location") or {}
        if not location:
            return []
        return [
            GeocodeCandidate(
                address=address,
                lng=float(location["lng"]),
                lat=float(location["lat"]),
                level=str(result.get("level", "")),
                precise=result.get("precise"),
                confidence=result.get("confidence"),
            )
        ]

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        records: list[PoiRecord] = []
        for page_num in range(max_pages):
            data = await self.request(
                "/place/v2/search",
                {
                    "query": query,
                    "location": f"{center[0]:.6f},{center[1]:.6f}",
                    "radius": str(radius_m),
                    "scope": "2",
                    "page_size": str(page_size),
                    "page_num": str(page_num),
                },
            )
            page = data.get("results") or []
            records.extend(self._parse_poi(item) for item in page if item.get("uid"))
            if len(page) < page_size:
                break  # 末页
        return records

    @staticmethod
    def _parse_poi(item: dict[str, Any]) -> PoiRecord:
        location = item.get("location") or {}
        return PoiRecord(
            uid=str(item["uid"]),
            name=str(item.get("name", "")),
            lng=float(location.get("lng", 0)),
            lat=float(location.get("lat", 0)),
            address=str(item.get("address", "") or ""),
            province=str(item.get("province", "") or ""),
            city=str(item.get("city", "") or ""),
            area=str(item.get("area", "") or ""),
            tag=str(item.get("tag", "") or ""),
        )

    # ---- 批量步行测距测时（等时圈探针）----

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        """routematrix/v2/walking：按 matrix_batch_size 分批，结果顺序与输入一致。

        分批各自走缓存/限流/重试（复用 _request），任一批不可重试失败即整体抛出，
        由引擎决定是否降级——铁律 #3：故障策略收口在适配器，语义决策留给编排层。
        """
        legs: list[RouteLeg] = []
        batch_size = max(1, self._s.matrix_batch_size)
        for start in range(0, len(destinations), batch_size):
            batch = destinations[start : start + batch_size]
            data = await self.request(
                "/routematrix/v2/walking",
                {
                    "origins": _fmt_points([origin]),
                    "destinations": _fmt_points(batch),
                },
            )
            results = data.get("result") or []
            # 响应按 origin×destination 笛卡尔积行优先排列；单源场景与输入同序
            for item in results:
                distance = (item.get("distance") or {}).get("value")
                duration = (item.get("duration") or {}).get("value")
                legs.append(
                    RouteLeg(
                        distance_m=float(distance) if distance is not None else None,
                        duration_s=float(duration) if duration is not None else None,
                    )
                )
        return legs

    async def close(self) -> None:
        await self._client.aclose()


def _fmt_points(points: list[BD09Point]) -> str:
    """routematrix 坐标串格式：lng,lat|lng,lat（百度侧默认 bd09ll）。"""
    return "|".join(f"{lng:.6f},{lat:.6f}" for lng, lat in points)
