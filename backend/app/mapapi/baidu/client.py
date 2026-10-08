"""百度地图 Web 服务 API 适配器。

限流/重试/熔断/缓存/SN 签名全部收口在此（CLAUDE.md 铁律 #3）：
- 令牌桶按配置限速，排队不丢弃；
- 连续瞬时失败达阈值熔断，打开期内未命中缓存的请求快速失败（零出站）；
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
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.breaker import CircuitBreaker
from app.core.budget import BudgetExceeded, current_budget
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
    # 302/401 = "当前并发量已经超过约定并发配额，限制访问"（RequestLimitExceeded）：
    # 瞬时并发限流而非配额耗尽，退避重试即可自愈。401 为实测捕获（2026-10-09 真实采集，
    # QPS=2 稳态连打第 5 批矩阵触发）；曾因未收录归 UNKNOWN 不可重试，导致批量矩阵
    # 失败、等时圈被迫整体降级（docs/bugfix/2026-10-09-routematrix-concurrency-limit.md）。
    302: ErrorKind.RATE_LIMIT,
    401: ErrorKind.RATE_LIMIT,
}


class BaiduClient:
    name = "baidu"

    def __init__(self, settings: Settings, cache: CacheBackend | None = None) -> None:
        self._s = settings
        self._log = structlog.get_logger(__name__)
        self._cache = cache or MemoryCache()
        self._bucket = TokenBucket(settings.api_qps, settings.api_burst)
        self._breaker = CircuitBreaker(settings.breaker_fail_threshold, settings.breaker_open_s)
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
        """单次请求：缓存 → 预算 → 熔断 → 限流 → HTTP → status 分类。

        缓存命中与熔断快速失败零出站：不占预算、不构成故障证据（docs/02 §3.4）。
        """
        request_params = {**params, "output": "json", "ak": self._s.baidu_ak}
        if self._s.baidu_sk:
            request_params["sn"] = self._calc_sn(path, request_params)

        key = cache_key("baidu", path=path, **params)
        cached = await self._cache.get(key)
        if cached is not None:
            budget = current_budget()
            if budget is not None:
                budget.record_cache_hit()
            return json.loads(cached)

        try:
            _charge_budget()
            if not self._breaker.allow():
                raise MapApiError(ErrorKind.BREAKER_OPEN, "熔断打开中，快速失败")
            await self._bucket.acquire()
            data = await self._roundtrip(path, key, request_params)
        except MapApiError as exc:
            # 仅瞬时错误计入熔断；BUDGET_EXCEEDED/BREAKER_OPEN 为本地拦截，非上游故障
            if exc.kind in RETRYABLE_KINDS:
                self._breaker.record_failure()
            raise
        finally:
            # 探针请求若被取消/被预算拦截（未走到 record_*），释放半开名额防卡死
            self._breaker.abandon_probe()
        self._breaker.record_success()
        return data

    async def _roundtrip(
        self, path: str, key: str, request_params: dict[str, str]
    ) -> dict[str, Any]:
        """单次出站 HTTP：状态码分类 → JSON 解析 → 百度 status 分类 → 写缓存。"""
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
            before_sleep=_count_retry,
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
        """地点检索 3.0 周边检索（/place/v3/around）。

        注意 v3 与 v2 的关键差异（2025 控制台升级后新 AK 权限仅挂 v3，
        v2 圆形检索对这类 AK 静默返回空结果，已实测踩坑）：
        - location 参数为「纬度,经度」，与 v2 的「经度,纬度」相反；
        - radius 仅是召回权重，必须加 radius_limit=true 才严格限定在半径内
          （生活圈覆盖语义要求严格限定）；
        - 顶层无 tag 字段，分类在 detail_info.classified_poi_tag（scope=2 时返回）。
        """
        records: list[PoiRecord] = []
        for page_num in range(max_pages):
            data = await self.request(
                "/place/v3/around",
                {
                    "query": query,
                    "location": f"{center[1]:.6f},{center[0]:.6f}",
                    "radius": str(radius_m),
                    "radius_limit": "true",
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
        detail = item.get("detail_info") or {}
        # v3 检索结果无顶层 tag，分类标签在 detail_info.classified_poi_tag
        tag = str(item.get("tag") or detail.get("classified_poi_tag") or "")
        return PoiRecord(
            uid=str(item["uid"]),
            name=str(item.get("name", "")),
            lng=float(location.get("lng", 0)),
            lat=float(location.get("lat", 0)),
            address=str(item.get("address", "") or ""),
            province=str(item.get("province", "") or ""),
            city=str(item.get("city", "") or ""),
            area=str(item.get("area", "") or ""),
            tag=tag,
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
            if len(results) != len(batch):
                # 行数与目的地数不符（上游截断/丢行）：缺失行无法对应回具体目的地，
                # 静默截断或补 None 都会把耗时错配到别的设施——按上游故障抛出，
                # 由编排层决定降级（铁律 #3：故障策略收口在适配器，语义决策在编排层）
                raise MapApiError(
                    ErrorKind.SERVER,
                    f"routematrix 响应行数 {len(results)} != 目的地数 {len(batch)}",
                )
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

    async def walking_route(self, origin: BD09Point, destination: BD09Point) -> RouteLeg:
        """direction/v2/walking 单对规划（降级链中间级，docs/02 §3.4）。

        响应 result.routes[0] 的 distance/duration 为裸数值（与 routematrix 的
        {value:} 包装不同）；解析兼容两种形态——降级链本身是对故障容错的位置，
        不为格式细节二次抛错（格式若有出入按 BF-004 流程实测修正）。
        无路线返回不可达 Leg。
        """
        data = await self.request(
            "/direction/v2/walking",
            {
                "origin": _fmt_points([origin]),
                "destination": _fmt_points([destination]),
            },
        )
        routes = (data.get("result") or {}).get("routes") or []
        if not routes:
            return RouteLeg(distance_m=None, duration_s=None)
        return RouteLeg(
            distance_m=_scalar(routes[0].get("distance")),
            duration_s=_scalar(routes[0].get("duration")),
        )

    async def close(self) -> None:
        await self._client.aclose()


def _fmt_points(points: list[BD09Point]) -> str:
    """routematrix 坐标串格式：lat,lng|lat,lng（该接口纬度在前，与 place v2 相反；
    传反会得到 status=2 参数非法，已实测）。"""
    return "|".join(f"{lat:.6f},{lng:.6f}" for lng, lat in points)


def _scalar(value: Any) -> float | None:
    """direction/routematrix 两代响应的数值提取：裸数值或 {value: x} 包装。"""
    if isinstance(value, dict):
        value = value.get("value")
    return float(value) if value is not None else None


def _charge_budget() -> None:
    """出站前占用预算名额；无作用域（独立路由/测试直连）时跳过。"""
    budget = current_budget()
    if budget is not None:
        try:
            budget.acquire()
        except BudgetExceeded as exc:
            raise MapApiError(
                ErrorKind.BUDGET_EXCEEDED, f"单次分析出站预算耗尽（上限 {exc.max_calls} 次）"
            ) from exc


def _count_retry(state: RetryCallState) -> None:
    """tenacity before_sleep 钩子：重试调度计数（docs/02 §7.5 重试开销统计）。"""
    budget = current_budget()
    if budget is not None:
        budget.record_retry()
