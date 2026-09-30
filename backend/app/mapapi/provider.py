"""MapProvider 协议（端口）。

域层与路由层只依赖本协议；百度实现见 baidu/client.py，快照回放见 replay/client.py。
新增地图能力（M2 的 route_matrix / walking_route）= 先在此扩展协议，再补两个实现与回放数据。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.models.geocode import GeocodeCandidate
from app.models.isochrone import RouteLeg
from app.models.poi import PoiRecord

BD09Point = tuple[float, float]


class ErrorKind(StrEnum):
    TIMEOUT = "TIMEOUT"  # 网络超时，可重试
    SERVER = "SERVER"  # 上游内部错误/5xx，可重试
    RATE_LIMIT = "RATE_LIMIT"  # 触发限流，可重试（退避）
    AUTH = "AUTH"  # AK 非法/被封禁，不可重试
    QUOTA = "QUOTA"  # 配额耗尽，不可重试（应降级/切 Key）
    BAD_REQUEST = "BAD_REQUEST"  # 参数错误，不可重试
    NO_RESULT = "NO_RESULT"  # 语义上无结果（非错误）
    UNKNOWN = "UNKNOWN"  # 未归类


RETRYABLE_KINDS = frozenset({ErrorKind.TIMEOUT, ErrorKind.SERVER, ErrorKind.RATE_LIMIT})


class MapApiError(Exception):
    """地图 API 统一异常。kind 决定重试/降级策略，status 为上游原始状态码。"""

    def __init__(self, kind: ErrorKind, message: str, status: int | None = None) -> None:
        super().__init__(f"[{kind.value}] {message}")
        self.kind = kind
        self.status = status


@runtime_checkable
class MapProvider(Protocol):
    """地图能力端口。所有坐标出入均为 BD09。"""

    name: str

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        """地址 → 坐标候选列表（0 个 = 未匹配，不算错误）。"""
        ...

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        """圆形区域关键词检索，内部完成翻页聚合。category 字段由调用方回填。"""
        ...

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        """单源批量步行测距测时（等时圈核心探针）。

        返回顺序与 destinations 一一对应；不可达目的地返回 distance/duration 为 None 的 Leg。
        实现方负责按批量上限分批（铁律 #7：能用批量不逐条）。
        """
        ...

    async def close(self) -> None:
        """释放底层连接资源。"""
        ...
