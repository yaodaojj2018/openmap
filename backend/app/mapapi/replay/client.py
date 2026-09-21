"""快照回放 Provider（演示模式 / CI）。

数据源 data/replays/demo.json：
- meta：示例地址 + 中心点（前端"加载示例"与 /demo/hint 使用）
- geocode：地址（含子串匹配）→ 坐标候选
- poi_pool：带检索关键词标签的 POI 池；search_pois 按关键词命中 + 半径过滤本地计算

全链路零 API 消耗。新增 Provider 方法必须同步扩展本实现与快照数据（CLAUDE.md 演示模式规则）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from app.core.coords import haversine_m
from app.mapapi.provider import BD09Point
from app.models.geocode import GeocodeCandidate
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

    async def close(self) -> None:
        return None
