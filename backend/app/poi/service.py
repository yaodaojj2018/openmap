"""POI 分类目检索与清洗（编排 + 纯函数清洗管道）。

Provider 由调用方注入（域层不自建 IO，CLAUDE.md 铁律 #2）；
清洗部分（过滤/去重/回填）为纯函数，单独可测。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.coords import haversine_m
from app.mapapi.provider import BD09Point, MapProvider
from app.models.poi import CategoryKey, PoiRecord


@dataclass(frozen=True)
class CategorySpec:
    """类目规格：检索词 + 噪音过滤正则（作用于"名称 标签"，剔除类目噪音）。"""

    key: CategoryKey
    label: str
    query: str
    filter_pattern: str


# 默认分类体系；可用 data/config/poi_taxonomy.json 覆盖（铁律 #4：外部约束配置化）
DEFAULT_TAXONOMY: tuple[CategorySpec, ...] = (
    CategorySpec("medical", "医疗（药店）", "药店", r"药"),
    CategorySpec("education", "教育（小学）", "小学", r"小学"),
    CategorySpec("shopping", "购物（菜市场）", "菜市场", r"菜市场|农贸市场"),
    CategorySpec("elderly", "养老", "养老院", r"养老"),
)


def load_taxonomy(path: str) -> tuple[CategorySpec, ...]:
    """从 JSON 加载分类体系，文件缺失/非法时回退默认（不阻断服务）。"""
    file = Path(path)
    if not file.exists():
        return DEFAULT_TAXONOMY
    raw = json.loads(file.read_text(encoding="utf-8"))
    specs = tuple(CategorySpec(**item) for item in raw["categories"])
    return specs or DEFAULT_TAXONOMY


def clean_records(
    records: list[PoiRecord],
    spec: CategorySpec,
    center: BD09Point,
    dedup_distance_m: float,
) -> list[PoiRecord]:
    """清洗管道（纯函数）：类目过滤 → uid/名称+距离去重 → 回填 category 与 distance_m。"""
    filtered = [r for r in records if re.search(spec.filter_pattern, f"{r.name} {r.tag}")]
    seen_uids: set[str] = set()
    name_anchor: dict[str, PoiRecord] = {}
    cleaned: list[PoiRecord] = []
    for rec in filtered:
        if rec.uid in seen_uids:
            continue
        seen_uids.add(rec.uid)
        anchor = name_anchor.get(rec.name)
        if anchor and haversine_m(anchor.lng, anchor.lat, rec.lng, rec.lat) < dedup_distance_m:
            continue  # 同名连锁/数据源重复（30m 内聚类，保留首个）
        name_anchor[rec.name] = rec
        cleaned.append(
            rec.model_copy(
                update={
                    "category": spec.key,
                    "distance_m": haversine_m(center[0], center[1], rec.lng, rec.lat),
                }
            )
        )
    cleaned.sort(key=lambda r: r.distance_m or 0.0)
    return cleaned


async def search_categories(
    provider: MapProvider,
    settings: Settings,
    taxonomy: tuple[CategorySpec, ...],
    center: BD09Point,
    category_keys: list[CategoryKey],
    radius_m: int,
) -> dict[str, list[PoiRecord]]:
    """按类目并发检索 + 清洗。各类目互不阻塞；单类目失败不拖垮整体。"""

    async def _one(spec: CategorySpec) -> tuple[str, list[PoiRecord]]:
        raw = await provider.search_pois(
            query=spec.query,
            center=center,
            radius_m=radius_m,
            page_size=settings.poi_page_size,
            max_pages=settings.poi_max_pages,
        )
        return spec.key, clean_records(raw, spec, center, settings.poi_dedup_distance_m)

    selected = [s for s in taxonomy if s.key in category_keys]
    results = await asyncio.gather(*(_one(s) for s in selected))
    return dict(results)
