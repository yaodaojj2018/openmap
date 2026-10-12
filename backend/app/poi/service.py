"""POI 分类目检索与清洗（编排 + 纯函数清洗管道）。

Provider 由调用方注入（域层不自建 IO，CLAUDE.md 铁律 #2）；
清洗部分（过滤/去重/回填）为纯函数，单独可测。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.coords import haversine_m
from app.mapapi.provider import BD09Point, MapProvider
from app.models.poi import CategoryKey, PoiRecord


@dataclass(frozen=True)
class CategorySpec:
    """类目规格：多检索词 OR 召回 + 噪音过滤正则（作用于"名称 标签"）。

    queries 为列表的原因：百度 v3 周边检索按分词索引匹配，"实验小学"类校名
    无法被 query="小学" 召回（实测该类学校仅门禁 POI 可被"学校"召回，
    见 docs/bugfix/2026-10-09-poi-primary-school-recall.md）；多词召回后
    按 uid 去重合并，代价是每多一个词多 1 次逻辑调用。
    """

    key: CategoryKey
    label: str
    queries: tuple[str, ...]
    filter_pattern: str


# 默认分类体系；可用 data/config/poi_taxonomy.json 覆盖（铁律 #4：外部约束配置化）
DEFAULT_TAXONOMY: tuple[CategorySpec, ...] = (
    CategorySpec("medical", "医疗（药店）", ("药店",), r"药"),
    CategorySpec("education", "教育（小学）", ("小学", "学校", "九年一贯制"), r"小学|九年一贯制"),
    CategorySpec("shopping", "购物（菜市场）", ("菜市场",), r"菜市场|农贸市场"),
    CategorySpec("elderly", "养老", ("养老院",), r"养老"),
)


def load_taxonomy(path: str) -> tuple[CategorySpec, ...]:
    """从 JSON 加载分类体系，文件缺失/非法时回退默认（不阻断服务）。"""
    file = Path(path)
    if not file.exists():
        return DEFAULT_TAXONOMY
    raw = json.loads(file.read_text(encoding="utf-8"))
    specs = tuple(
        CategorySpec(**{**item, "queries": tuple(item["queries"])}) for item in raw["categories"]
    )
    return specs or DEFAULT_TAXONOMY


# 附属 POI：门禁/楼栋等学校或设施的附属点位（tag 特征），其名称 = 主体名 + 方位门/教学楼后缀。
# "实验小学"类校名在百度周边检索索引里只有门禁 POI 可被召回，故附属不能一刀切剔除——
# 主体在场时坍缩（同校只计一次），主体缺席时保留最近一个附属作为该校代表。
_ATTACHED_TAG = re.compile(r"出入口|校内设施")
_ATTACHED_SUFFIX = re.compile(r"[-－—\s]*(?:[东南西北]{1,2}\d*门|教学楼.*)$")


def _base_name(name: str) -> str:
    """附属 POI 名称 → 主体名称（剥离"-南门"/"西门"/"教学楼-3幢"后缀）。"""
    return _ATTACHED_SUFFIX.sub("", name)


def _collapse_attached(records: list[PoiRecord]) -> list[PoiRecord]:
    """按主体名坍缩附属 POI：主体在场剔附属；缺席保留距离最近的一个附属代表。"""
    attached: dict[str, list[PoiRecord]] = {}
    mains: set[str] = set()
    for rec in records:
        if _ATTACHED_TAG.search(rec.tag):
            attached.setdefault(_base_name(rec.name), []).append(rec)
        else:
            mains.add(_base_name(rec.name))
    drop: set[str] = set()
    for base, group in attached.items():
        if base in mains:
            drop.update(rec.uid for rec in group)
        else:
            group.sort(key=lambda r: r.distance_m or 0.0)
            drop.update(rec.uid for rec in group[1:])
    return [rec for rec in records if rec.uid not in drop]


def clean_records(
    records: list[PoiRecord],
    spec: CategorySpec,
    center: BD09Point,
    dedup_distance_m: float,
) -> list[PoiRecord]:
    """清洗管道（纯函数）：类目过滤 → uid/名称+距离去重 → 附属 POI 坍缩 → 回填排序。"""
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
    cleaned = _collapse_attached(cleaned)
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
    """按类目并发检索 + 清洗。各类目互不阻塞；单类目失败不拖垮整体。

    类目内多检索词顺序检索后合并——uid 去重收口在 clean_records，
    与真实百度"学校"能召回普通小学的行为对齐（回放池 queries 同步双标）。
    """

    async def _one(spec: CategorySpec) -> tuple[str, list[PoiRecord]]:
        raw: list[PoiRecord] = []
        for query in spec.queries:
            raw.extend(
                await provider.search_pois(
                    query=query,
                    center=center,
                    radius_m=radius_m,
                    page_size=settings.poi_page_size,
                    max_pages=settings.poi_max_pages,
                )
            )
        return spec.key, clean_records(raw, spec, center, settings.poi_dedup_distance_m)

    selected = [s for s in taxonomy if s.key in category_keys]
    results = await asyncio.gather(*(_one(s) for s in selected))
    return dict(results)


async def search_landuse_pois(
    provider: MapProvider,
    settings: Settings,
    center: BD09Point,
    radius_m: int,
    queries: Sequence[str],
) -> list[PoiRecord]:
    """按关键词组检索用地 POI 并去重（居住正向掩膜与工业园负向修正共用，docs/06 §6）。

    与 search_categories 的差异：用地不是业务类目（不进 taxonomy、无 filter_pattern
    清洗）——query 本身已高度相关（2026-10-11/12 实测：小区/住宅/楼盘打满 40；工业园/
    产业园/科技园命中 20/6/16），只需去重。失败抛 MapApiError，由编排层决定降级
    （BF-007：数据缺失 ≠ 事实结论）。
    """
    raw: list[PoiRecord] = []
    for query in queries:
        raw.extend(
            await provider.search_pois(
                query=query,
                center=center,
                radius_m=radius_m,
                page_size=settings.poi_page_size,
                max_pages=settings.poi_max_pages,
            )
        )
    return dedup_landuse_pois(raw, center, settings.poi_dedup_distance_m)


def dedup_landuse_pois(
    records: list[PoiRecord], center: BD09Point, dedup_distance_m: float
) -> list[PoiRecord]:
    """用地 POI 去重（纯函数）：uid 去重 + 同名 30m 内聚类，回填 distance_m 排序。

    多关键词召回高度重叠（同名同 uid 各词都命中），去重保证 cKDTree 不被重复点污染
    （不影响最近距离结果，但避免无谓的重复建树开销）。
    """
    seen_uids: set[str] = set()
    name_anchor: dict[str, PoiRecord] = {}
    out: list[PoiRecord] = []
    for rec in records:
        if rec.uid in seen_uids:
            continue
        seen_uids.add(rec.uid)
        anchor = name_anchor.get(rec.name)
        if anchor and haversine_m(anchor.lng, anchor.lat, rec.lng, rec.lat) < dedup_distance_m:
            continue
        name_anchor[rec.name] = rec
        out.append(
            rec.model_copy(
                update={"distance_m": haversine_m(center[0], center[1], rec.lng, rec.lat)}
            )
        )
    out.sort(key=lambda r: r.distance_m or 0.0)
    return out
