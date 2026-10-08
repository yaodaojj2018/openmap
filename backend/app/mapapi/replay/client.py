"""快照回放 Provider（演示模式 / CI）。

数据源 data/replays/demo.json：
- meta：示例地址 + 中心点（前端"加载示例"与 /demo/hint 使用）
- geocode：地址（含子串匹配）→ 坐标候选
- poi_pool：带检索关键词标签的 POI 池；search_pois 按关键词命中 + 半径过滤本地计算
- isochrone：route_matrix 确定性模拟参数（16 方向绕行系数 detour + 阻挡上限 cap_m），
  制造非圆形等时圈与河流阻挡凹陷，保证演示效果与测试可断言

全链路零 API 消耗。新增 Provider 方法必须同步扩展本实现与快照数据（CLAUDE.md 演示模式规则）。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from app.core.coords import M_PER_DEG, haversine_m
from app.mapapi.provider import BD09Point
from app.models.geocode import GeocodeCandidate
from app.models.isochrone import RouteLeg
from app.models.poi import PoiRecord


class ReplayProvider:
    name = "replay"

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._snap = snapshot
        self._log = structlog.get_logger(__name__)

    @classmethod
    def from_file(cls, path: str) -> ReplayProvider:
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(
                f"回放快照不存在: {path}（demo 模式依赖 data/replays/demo.json）"
            )
        return cls(json.loads(file.read_text(encoding="utf-8")))

    @property
    def meta(self) -> dict[str, Any]:
        return self._snap.get("meta", {})

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        for key, cand in self._snap.get("geocode", {}).items():
            if key in address or address in key:
                return [GeocodeCandidate(address=key, **cand)]
        self._log.warning("replay.geocode_miss", address=address)
        return []

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        hits: list[PoiRecord] = []
        for item in self._snap.get("poi_pool", []):
            if query not in item.get("queries", []):
                continue
            if haversine_m(center[0], center[1], item["lng"], item["lat"]) > radius_m:
                continue
            hits.append(
                PoiRecord(
                    uid=item["uid"],
                    name=item["name"],
                    lng=item["lng"],
                    lat=item["lat"],
                    address=item.get("address", ""),
                    tag=item.get("tag", ""),
                )
            )
        return hits[: max_pages * page_size]

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        """确定性步行测时模拟：直线距离 × 方向绕行系数 ÷ 步速；超出方向 cap 视为不可达。

        detour/cap 按方位角从 isochrone.profile 双向环绕线性插值，
        使相邻方向过渡平滑（凹陷无锯齿）。
        """
        simulate = self._simulator()
        return [simulate(origin, (lng, lat)) for lng, lat in destinations]

    async def walking_route(self, origin: BD09Point, destination: BD09Point) -> RouteLeg:
        """单对 OD 模拟：与 route_matrix 完全同模型（降级链中间级的回放口径）。

        复用 isochrone 快照的 speed/detour/cap 参数——两方法对同一 OD 必须给出
        一致的模拟结果，否则降级前后判定口径漂移，测试无法断言。
        """
        return self._simulator()(origin, destination)

    def _simulator(self) -> Callable[[BD09Point, BD09Point], RouteLeg]:
        """构建单点测时模拟闭包（route_matrix / walking_route 共用）。"""
        profile = self._snap.get("isochrone") or {}
        speed = float(profile.get("speed_m_s", 1.35))
        nodes: list[tuple[float, float, float]] = [
            (
                math.radians(float(p["theta_deg"])),
                float(p.get("detour", 1.0)),
                float(p.get("cap_m", 10_000.0)),
            )
            for p in profile.get("profile", [])
        ]

        def simulate(origin: BD09Point, target: BD09Point) -> RouteLeg:
            straight = haversine_m(origin[0], origin[1], target[0], target[1])
            if nodes:
                theta = _bearing_rad(origin, target)
                detour, cap = _interp_profile(nodes, theta)
            else:
                detour, cap = 1.0, 10_000.0
            if straight > cap:
                return RouteLeg(distance_m=None, duration_s=None)
            distance = straight * detour
            return RouteLeg(distance_m=distance, duration_s=distance / speed)

        return simulate

    async def close(self) -> None:
        return None


def _bearing_rad(origin: BD09Point, target: BD09Point) -> float:
    """局部平面方位角（弧度，正东为 0 逆时针），与 sampler 的极坐标约定一致。

    必须在米空间计算：度空间 atan2 会因经纬两轴压缩系数不同（cos 纬度）
    产生可达 ±8° 的角度畸变，使方向插值取到错误的 detour/cap。
    """
    dx = (target[0] - origin[0]) * M_PER_DEG * math.cos(math.radians(origin[1]))
    dy = (target[1] - origin[1]) * M_PER_DEG
    return math.atan2(dy, dx)


def _interp_profile(nodes: list[tuple[float, float, float]], theta: float) -> tuple[float, float]:
    """在环绕排序的 profile 节点间对 (detour, cap) 做线性插值。"""
    ordered = sorted(nodes)
    if not ordered:
        return 1.0, 10_000.0
    tau = 2 * math.pi
    theta %= tau
    for i, cur in enumerate(ordered):
        nxt = ordered[(i + 1) % len(ordered)]
        span = (nxt[0] - cur[0]) % tau
        if span <= 0:
            continue
        offset = (theta - cur[0]) % tau
        if offset <= span:
            ratio = offset / span
            detour = cur[1] + (nxt[1] - cur[1]) * ratio
            cap = cur[2] + (nxt[2] - cur[2]) * ratio
            return detour, cap
    return ordered[-1][1], ordered[-1][2]
