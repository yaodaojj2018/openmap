"""百度适配器集成测试：respx mock 覆盖正常链路与异常矩阵（docs/02 §7.2）。"""

import httpx
import pytest
import respx

from app.core.cache import MemoryCache
from app.core.config import Settings
from app.mapapi.baidu.client import BaiduClient
from app.mapapi.provider import ErrorKind, MapApiError

GEOCODE_URL = "https://api.map.baidu.com/geocoding/v3/"
PLACE_URL = "https://api.map.baidu.com/place/v2/search"


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
    return {
        "status": 0,
        "results": [
            {
                "uid": f"uid-{i}",
                "name": f"模拟药店{i:03d}",
                "location": {"lng": 116.31 + i * 1e-5, "lat": 39.98},
                "address": "模拟地址",
                "tag": "药店",
            }
            for i in range(start_index, start_index + n)
        ],
    }


async def test_poi_pagination_aggregates() -> None:
    """翻页聚合：首页满页 20 条 → 次页 3 条 → 共 23 条，第三页不再请求。"""
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
        assert route.call_count == 2
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
