"""百度适配器集成测试：respx mock 覆盖正常链路与异常矩阵（docs/02 §7.2）。"""

import asyncio

import httpx
import pytest
import respx

from app.core.budget import budget_scope
from app.core.cache import MemoryCache
from app.core.config import Settings
from app.mapapi.baidu.client import BaiduClient
from app.mapapi.provider import ErrorKind, MapApiError

GEOCODE_URL = "https://api.map.baidu.com/geocoding/v3/"
PLACE_URL = "https://api.map.baidu.com/place/v3/around"
ROUTEMATRIX_URL = "https://api.map.baidu.com/routematrix/v2/walking"
WALKING_URL = "https://api.map.baidu.com/direction/v2/walking"


def make_client(**overrides: object) -> BaiduClient:
    settings = Settings(baidu_ak="test-ak", retry_backoff_s=0.0, **overrides)
    return BaiduClient(settings, MemoryCache())


def geocode_payload(status: int = 0, lng: float = 116.316628, lat: float = 39.981909) -> dict:
    if status != 0:
        return {"status": status, "message": "mock error"}
    return {
        "status": 0,
        "result": {"location": {"lng": lng, "lat": lat}, "level": "门址", "precise": 1},
    }


async def test_geocode_ok() -> None:
    with respx.mock:
        respx.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json=geocode_payload()))
        client = make_client()
        candidates = await client.geocode("北京市海淀区中关村大街1号")
        assert len(candidates) == 1
        assert candidates[0].lng == pytest.approx(116.316628)
        assert candidates[0].level == "门址"
        await client.close()


async def test_quota_error_not_retried() -> None:
    """配额类错误（status=4）不可重试：只调用一次即抛出。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(
            return_value=httpx.Response(200, json=geocode_payload(status=4))
        )
        client = make_client()
        with pytest.raises(MapApiError) as exc_info:
            await client.geocode("某地址")
        assert exc_info.value.kind == ErrorKind.QUOTA
        assert route.call_count == 1
        await client.close()


async def test_server_error_retried_then_ok() -> None:
    """服务端错误（status=1）可重试：第一次失败、第二次成功。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(
            side_effect=[
                httpx.Response(200, json=geocode_payload(status=1)),
                httpx.Response(200, json=geocode_payload()),
            ]
        )
        client = make_client()
        candidates = await client.geocode("某地址")
        assert len(candidates) == 1
        assert route.call_count == 2
        await client.close()


@pytest.mark.regression
@pytest.mark.parametrize("status", [302, 401])
async def test_concurrency_limit_status_retried_then_ok(status: int) -> None:
    """并发限流（status=302/401"当前并发量已超约定并发配额"）按 RATE_LIMIT 退避重试自愈。

    回归（BF-009）：routematrix 的并发限流码曾未收录归 UNKNOWN（不可重试、不构成
    熔断证据），QPS=2 稳态连打第 5 批矩阵即触发，批量矩阵直接失败、等时圈整体
    降级直线估算——本应退避 0.5s 重试一次即自愈。
    """
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(
            side_effect=[
                httpx.Response(200, json=geocode_payload(status=status)),
                httpx.Response(200, json=geocode_payload()),
            ]
        )
        client = make_client()
        candidates = await client.geocode("某地址")
        assert len(candidates) == 1
        assert route.call_count == 2, "并发限流必须退避重试而非直接失败"
        await client.close()


async def test_timeout_retries_exhausted() -> None:
    """超时连续 3 次（默认重试上限）后抛 TIMEOUT。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(side_effect=httpx.ReadTimeout("mock timeout"))
        client = make_client()
        with pytest.raises(MapApiError) as exc_info:
            await client.geocode("某地址")
        assert exc_info.value.kind == ErrorKind.TIMEOUT
        assert route.call_count == 3
        await client.close()


def place_page(n: int, start_index: int = 0) -> dict:
    """v3/around 风格响应：分类标签在 detail_info.classified_poi_tag（无顶层 tag）。"""
    return {
        "status": 0,
        "results": [
            {
                "uid": f"uid-{i}",
                "name": f"模拟药店{i:03d}",
                "location": {"lng": 116.31 + i * 1e-5, "lat": 39.98},
                "address": "模拟地址",
                "detail_info": {"classified_poi_tag": "医疗;药店"},
            }
            for i in range(start_index, start_index + n)
        ],
    }


@pytest.mark.regression
async def test_poi_pagination_aggregates() -> None:
    """翻页聚合：首页满页 20 条 → 次页 3 条 → 共 23 条，第三页不再请求。

    回归（BF-004）：v3/around 关键差异——location 为 纬度,经度（与 v2 相反）、
    radius_limit=true 严格限定。新控制台 AK 权限挂在 3.0，v2 圆形检索静默返回空。
    """
    with respx.mock:
        route = respx.get(PLACE_URL).mock(
            side_effect=[
                httpx.Response(200, json=place_page(20)),
                httpx.Response(200, json=place_page(3, start_index=20)),
            ]
        )
        client = make_client()
        records = await client.search_pois("药店", (116.316628, 39.981909), 1300, 20, 5)
        assert len(records) == 23
        assert records[0].tag == "医疗;药店"
        assert route.call_count == 2

        # v3 关键差异回归：location 为 纬度,经度；radius_limit 严格限定
        params = route.calls[0].request.url.params
        assert params["location"] == "39.981909,116.316628"
        assert params["radius_limit"] == "true"
        await client.close()


async def test_poi_cached_on_second_call() -> None:
    """相同参数二次检索命中缓存，不再发起上游请求。"""
    with respx.mock:
        route = respx.get(PLACE_URL).mock(return_value=httpx.Response(200, json=place_page(3)))
        client = make_client()
        await client.search_pois("药店", (116.316628, 39.981909), 1300, 20, 5)
        records = await client.search_pois("药店", (116.316628, 39.981909), 1300, 20, 5)
        assert len(records) == 3
        assert route.call_count == 1  # 第二次走缓存
        await client.close()


def matrix_payload(n: int, unreachable_last: bool = False) -> dict:
    results = [
        {"distance": {"value": 100 * (i + 1)}, "duration": {"value": 60 * (i + 1)}}
        for i in range(n)
    ]
    if unreachable_last:
        results[-1] = {}  # 百度侧不可达目的地返回空元素
    return {"status": 0, "result": results}


@pytest.mark.regression
async def test_route_matrix_parses_and_formats_params() -> None:
    """参数格式（origins/destinations 坐标串）与结果顺序一一对应。

    回归（BF-004）：routematrix 坐标串为 纬度,经度（官方示例 origins=40.45,116.41），
    传反会 status=2 参数非法，等时圈全链路失败。
    """
    with respx.mock:
        route = respx.get(ROUTEMATRIX_URL).mock(
            return_value=httpx.Response(200, json=matrix_payload(3))
        )
        client = make_client()
        dests = [(116.32, 39.98), (116.33, 39.98), (116.34, 39.98)]
        legs = await client.route_matrix((116.316628, 39.981909), dests)
        assert [leg.duration_s for leg in legs] == [60.0, 120.0, 180.0]
        assert [leg.distance_m for leg in legs] == [100.0, 200.0, 300.0]

        params = route.calls[0].request.url.params
        # routematrix 坐标格式为 纬度,经度（与 place v2 相反），传反会 status=2
        assert params["origins"] == "39.981909,116.316628"
        assert (
            params["destinations"]
            == "39.980000,116.320000|39.980000,116.330000|39.980000,116.340000"
        )
        await client.close()


async def test_route_matrix_batches_by_limit() -> None:
    """超过 matrix_batch_size 自动分批，跨批结果顺序保持。"""
    with respx.mock:
        route = respx.get(ROUTEMATRIX_URL).mock(
            side_effect=[
                httpx.Response(200, json=matrix_payload(2)),
                httpx.Response(200, json=matrix_payload(1, unreachable_last=True)),
            ]
        )
        client = make_client(matrix_batch_size=2)
        dests = [(116.32, 39.98), (116.33, 39.98), (116.34, 39.98)]
        legs = await client.route_matrix((116.316628, 39.981909), dests)
        assert route.call_count == 2
        assert [leg.duration_s for leg in legs] == [60.0, 120.0, None]
        assert legs[2].reachable is False
        await client.close()


@pytest.mark.regression
async def test_route_matrix_truncated_rows_raise() -> None:
    """响应行数少于目的地数 → SERVER 错误，不得静默透传错位结果。

    回归（BF-006）：routematrix v2 可能省略不可步行/非法坐标的结果行；
    适配器若原样透传，下游 zip(strict) 抛 ValueError 击穿"只捕 MapApiError"
    的降级守卫，整个分析任务在等时圈+POI 预算花完后报废。
    """
    with respx.mock:
        respx.get(ROUTEMATRIX_URL).mock(return_value=httpx.Response(200, json=matrix_payload(2)))
        client = make_client()
        dests = [(116.32, 39.98), (116.33, 39.98), (116.34, 39.98)]
        with pytest.raises(MapApiError) as exc_info:
            await client.route_matrix((116.316628, 39.981909), dests)
        assert exc_info.value.kind == ErrorKind.SERVER
        await client.close()


# ---- 熔断（docs/02 §3.4：连续瞬时失败 → 打开期内快速失败，零出站）----


async def test_breaker_opens_and_fast_fails_without_http() -> None:
    """连续瞬时失败达阈值 → 熔断打开：后续请求快速失败且不再出站、不触发重试。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(side_effect=httpx.ReadTimeout("mock timeout"))
        client = make_client(breaker_fail_threshold=4, breaker_open_s=60.0)
        with pytest.raises(MapApiError) as first:
            await client.geocode("地址A")  # 3 次重试耗尽 → TIMEOUT；熔断计 3 次仍闭合
        assert first.value.kind == ErrorKind.TIMEOUT
        assert route.call_count == 3
        with pytest.raises(MapApiError) as second:
            await client.geocode("地址B")  # 第 4 次失败打开；重试第 5 次尝试被快速失败
        assert second.value.kind == ErrorKind.BREAKER_OPEN
        assert route.call_count == 4
        with pytest.raises(MapApiError) as third:
            await client.geocode("地址C")  # 打开期：零出站
        assert third.value.kind == ErrorKind.BREAKER_OPEN
        assert route.call_count == 4
        await client.close()


async def test_breaker_open_still_serves_cache() -> None:
    """熔断只挡出站：缓存命中照常返回（零出站不构成故障证据）。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(
            side_effect=[
                httpx.Response(200, json=geocode_payload()),
                httpx.ReadTimeout("mock timeout"),
                httpx.ReadTimeout("mock timeout"),
            ]
        )
        client = make_client(breaker_fail_threshold=2, breaker_open_s=60.0)
        assert len(await client.geocode("地址A")) == 1  # 成功并写缓存
        with pytest.raises(MapApiError) as exc_info:
            await client.geocode("地址B")  # 2 次失败 → 打开，第 3 次尝试快速失败
        assert exc_info.value.kind == ErrorKind.BREAKER_OPEN
        assert route.call_count == 3
        candidates = await client.geocode("地址A")  # 缓存命中，不受熔断影响
        assert len(candidates) == 1
        assert route.call_count == 3
        await client.close()


async def test_breaker_half_open_probe_recovers() -> None:
    """打开到期后半开放单探针，成功即闭合恢复出站。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(
            side_effect=[
                httpx.ReadTimeout("mock timeout"),
                httpx.ReadTimeout("mock timeout"),
                httpx.Response(200, json=geocode_payload()),
            ]
        )
        client = make_client(breaker_fail_threshold=2, breaker_open_s=0.01)
        with pytest.raises(MapApiError):
            await client.geocode("地址A")  # 打开
        await asyncio.sleep(0.02)  # 越过 open_s（httpx 路径无法注入假时钟，真实微等待）
        candidates = await client.geocode("地址C")  # 未缓存 → 半开探针
        assert len(candidates) == 1
        assert route.call_count == 3
        await client.close()


# ---- 逐条步行规划（docs/02 §3.4 降级链第二级）----


async def test_walking_route_parses_bare_numbers() -> None:
    """direction/v2/walking：routes[0] 裸数值口径 + 坐标串 纬度,经度。"""
    with respx.mock:
        route = respx.get(WALKING_URL).mock(
            return_value=httpx.Response(
                200, json={"status": 0, "result": {"routes": [{"distance": 1234, "duration": 910}]}}
            )
        )
        client = make_client()
        leg = await client.walking_route((116.316628, 39.981909), (116.326628, 39.981909))
        assert (leg.distance_m, leg.duration_s) == (1234.0, 910.0)
        params = route.calls[0].request.url.params
        assert params["origin"] == "39.981909,116.316628"
        assert params["destination"] == "39.981909,116.326628"
        await client.close()


async def test_walking_route_accepts_value_wrapper_and_unreachable() -> None:
    """兼容 {value:} 包装形态（与 routematrix 同构）；无路线返回不可达 Leg。"""
    with respx.mock:
        respx.get(WALKING_URL).mock(
            return_value=httpx.Response(
                200,
                json={"status": 0, "result": {"routes": [{"distance": {"value": 500}}]}},
            )
        )
        client = make_client()
        leg = await client.walking_route((116.316628, 39.981909), (116.326628, 39.981909))
        assert leg.distance_m == 500.0
        assert leg.duration_s is None  # 缺 duration 字段不虚构耗时
        assert leg.reachable is False
        await client.close()

        respx.get(WALKING_URL).mock(
            return_value=httpx.Response(200, json={"status": 0, "result": {"routes": []}})
        )
        client2 = make_client()
        empty = await client2.walking_route((116.316628, 39.981909), (116.326628, 39.981909))
        assert (empty.distance_m, empty.duration_s) == (None, None)
        await client2.close()


# ---- QuotaBudget（docs/02 §3.4：单次分析出站 HTTP 硬上限）----


async def test_budget_blocks_outbound_beyond_limit() -> None:
    """预算耗尽 → BUDGET_EXCEEDED（本地拦截不可重试，立即抛出），此后零出站。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(side_effect=httpx.ReadTimeout("mock timeout"))
        client = make_client()
        with budget_scope(2) as budget:
            with pytest.raises(MapApiError) as exc_info:
                await client.geocode("地址A")  # 2 次出站后，第 3 次尝试被护栏拦截
            assert exc_info.value.kind == ErrorKind.BUDGET_EXCEEDED
            assert route.call_count == 2
            assert budget.http_calls == 2
        await client.close()


async def test_budget_cache_hit_not_charged() -> None:
    """缓存命中零出站：不占预算名额，仅计入命中率统计。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(
            return_value=httpx.Response(200, json=geocode_payload())
        )
        client = make_client()
        with budget_scope(1) as budget:
            await client.geocode("地址A")
            await client.geocode("地址A")  # 缓存命中
            assert budget.http_calls == 1
            assert budget.cache_hits == 1
            with pytest.raises(MapApiError) as exc_info:
                await client.geocode("地址B")  # 新参数需出站，预算已满
            assert exc_info.value.kind == ErrorKind.BUDGET_EXCEEDED
            assert route.call_count == 1
        await client.close()


async def test_budget_counts_retry_outbound() -> None:
    """重试同样消耗出站名额：3 次物理出站 = 1 次首试 + 2 次重试调度。"""
    with respx.mock:
        route = respx.get(GEOCODE_URL).mock(side_effect=httpx.ReadTimeout("mock timeout"))
        client = make_client()
        with budget_scope(3) as budget:
            with pytest.raises(MapApiError) as exc_info:
                await client.geocode("地址A")  # 默认重试上限 3 次全部耗尽
            assert exc_info.value.kind == ErrorKind.TIMEOUT
            assert budget.http_calls == 3
            assert budget.retries == 2
            assert route.call_count == 3
        await client.close()
