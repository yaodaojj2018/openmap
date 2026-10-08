"""覆盖判定三级漏斗（docs/02 §3.2，纯函数域层，无 IO）。

成本控制逻辑：
- 一级·几何粗筛（零成本）：等时圈多边形外 → 直接判圈外，定论；
- 二级·时间场插值（零成本）：圈内点由注入的估算器（isochrone 时间场）出 t̂，
  远离阈值带即以插值口径定论，置信度标注 0.8；
- 三级·边缘带精判（API 封顶）：t̂ 落在阈值 ± band 或估算不可用（阻挡方向）的设施
  收集为待精判清单，由编排层批量矩阵实测定论（上限与批次由配置约束）。

API 调用发生在编排层（tasks/pipeline），本模块只产出"待精判清单"与合并逻辑，
保证域层可离线单测（铁律 #2）。
"""

from __future__ import annotations

from collections.abc import Callable

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.models.coverage import CategoryCoverage, CoverageMethod, FacilityCoverage
from app.models.isochrone import RouteLeg
from app.models.poi import PoiRecord

TimeEstimator = Callable[[float, float], float | None]
"""时间场估算器：(lng, lat) → 步行秒数；阻挡方向/无样本返回 None。由编排层注入。"""


def build_polygon(rings: list[list[list[float]]]) -> BaseGeometry | None:
    """GeoJSON 环组（外环 + 内环）→ shapely 多边形；空环返回 None（跳过一级粗筛）。"""
    if not rings or not rings[0]:
        return None
    return Polygon(shell=rings[0], holes=rings[1:] or None)


def classify_facilities(
    records: list[PoiRecord],
    polygon: BaseGeometry | None,
    estimator: TimeEstimator,
    threshold_s: float,
    band_lo_s: float,
    band_hi_s: float,
    field_confidence: float,
) -> tuple[list[FacilityCoverage], list[int]]:
    """跑一二级漏斗，产出逐设施判定 + 待三级精判的下标清单。

    待精判排序：估算不可用（阻挡方向）优先，其次 |t̂ - 阈值| 最小——精判配额
    有限时优先花在最不确定的设施上。未获精判的设施保留插值口径判定
    （估算不可用者保留保守圈外判定，由实测纠偏）。
    """
    verdicts: list[FacilityCoverage] = []
    edge_indices: list[int] = []
    for idx, rec in enumerate(records):
        inside = polygon is not None and polygon.contains(Point(rec.lng, rec.lat))
        if polygon is not None and not inside:
            verdicts.append(
                FacilityCoverage(
                    uid=rec.uid,
                    name=rec.name,
                    category=rec.category or "unknown",
                    est_walk_time_min=None,
                    in_circle=False,
                    method=CoverageMethod.POLYGON,
                    confidence=1.0,
                )
            )
            continue

        est_s = estimator(rec.lng, rec.lat)
        est_min = est_s / 60 if est_s is not None else None
        # 估算不可用（阻挡方向）：保守判圈外（置信度减半），进边缘带队列待实测纠偏。
        # 未获精判（配额截断/矩阵降级）时宁可少算不可多算——几何先验会把"未知"
        # 冒充成可达，系统性抬高 reachable 与评分
        provisional = FacilityCoverage(
            uid=rec.uid,
            name=rec.name,
            category=rec.category or "unknown",
            est_walk_time_min=est_min,
            in_circle=est_s is not None and est_s <= threshold_s,
            method=CoverageMethod.FIELD,
            confidence=field_confidence if est_s is not None else field_confidence / 2,
        )
        if est_s is None or band_lo_s <= est_s <= band_hi_s:
            edge_indices.append(idx)
        verdicts.append(provisional)

    threshold_min = threshold_s / 60
    edge_indices.sort(
        key=lambda i: (
            verdicts[i].est_walk_time_min is not None,  # 不可估算（阻挡方向）优先精判
            abs((verdicts[i].est_walk_time_min or 0.0) - threshold_min),
        )
    )
    return verdicts, edge_indices


def merge_matrix_verdicts(
    verdicts: list[FacilityCoverage],
    edge_indices: list[int],
    legs: list[RouteLeg],
    threshold_s: float,
) -> list[FacilityCoverage]:
    """三级漏斗收口：批量矩阵实测覆写边缘带设施的判定（实测为确定口径）。"""
    out = list(verdicts)
    for idx, leg in zip(edge_indices, legs, strict=True):
        est_min = leg.duration_s / 60 if leg.duration_s is not None else None
        out[idx] = out[idx].model_copy(
            update={
                "est_walk_time_min": est_min,
                "in_circle": leg.duration_s is not None and leg.duration_s <= threshold_s,
                "method": CoverageMethod.MATRIX,
                "confidence": 1.0,
            }
        )
    return out


def summarize_categories(
    verdicts: list[FacilityCoverage],
    labels: dict[str, str],
    sufficient_count: int,
) -> list[CategoryCoverage]:
    """按类目聚合统计与评分（公式见 CategoryCoverage docstring）。"""
    grouped: dict[str, list[FacilityCoverage]] = {key: [] for key in labels}
    for verdict in verdicts:
        grouped.setdefault(verdict.category, []).append(verdict)

    stats: list[CategoryCoverage] = []
    for key, label in labels.items():
        items = grouped.get(key, [])
        reachable = [v for v in items if v.in_circle]
        ratio = len(reachable) / len(items) if items else 0.0
        times = [v.est_walk_time_min for v in reachable if v.est_walk_time_min is not None]
        score = (
            100.0 * (0.6 * ratio + 0.4 * min(1.0, len(reachable) / sufficient_count))
            if items
            else 0.0
        )
        stats.append(
            CategoryCoverage(
                category=key,
                label=label,
                total=len(items),
                reachable=len(reachable),
                coverage_ratio=ratio,
                avg_walk_time_min=sum(times) / len(times) if times else None,
                score=round(score, 1),
            )
        )
    return stats
