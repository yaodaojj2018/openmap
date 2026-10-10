"""多源等时圈并集单元测试：_rings_union 与 _merge_iso 聚合语义。

单源 = 透传；多源 = 逐级 unary_union 成 MultiPolygon、probe/matrix_batches 求和、
confidence 取最小、degraded_reason 取首因（docs/02 §3.1）。
"""

import math

import pytest

from app.isochrone.engine import IsochroneComputation
from app.isochrone.field import TimeField
from app.models.common import Coord
from app.models.isochrone import IsochroneLevel, IsochroneResult
from app.tasks.pipeline import _merge_iso, _rings_union


def _level(
    minute: int, polygons: list[list[list[list[float]]]], confidence: float = 1.0
) -> IsochroneLevel:
    return IsochroneLevel(
        level_min=minute, coordinates=polygons, area_km2=1.0, confidence=confidence
    )


def _comp(
    lng: float,
    levels: list[IsochroneLevel],
    probe: int,
    batches: int,
    degraded: str | None = None,
) -> IsochroneComputation:
    result = IsochroneResult(
        origin=Coord(lng=lng, lat=39.98, crs="bd09"),
        origins=[Coord(lng=lng, lat=39.98, crs="bd09")],
        levels=levels,
        probe_count=probe,
        matrix_batches=batches,
        degraded_reason=degraded,
    )
    return IsochroneComputation(result=result, field=TimeField(16))


def test_rings_union_disjoint_squares() -> None:
    sq1 = [[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]]
    sq2 = [[(10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (11.0, 10.0), (10.0, 10.0)]]
    union = _rings_union([sq1, sq2])
    assert union is not None
    assert union.geom_type == "MultiPolygon"


def test_rings_union_empty_returns_none() -> None:
    assert _rings_union([]) is None
    assert _rings_union([[]]) is None


def test_merge_iso_single_source_passthrough() -> None:
    sq = [[(0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0), (0.0, 0.0)]]
    comp = _comp(116.0, [_level(15, [sq], confidence=0.9)], probe=10, batches=2)
    merged = _merge_iso([comp], [15], (116.0, 39.98), [Coord(lng=116.0, lat=39.98, crs="bd09")])
    assert merged.probe_count == 10
    assert merged.matrix_batches == 2
    assert merged.levels[0].confidence == 0.9
    assert len(merged.levels[0].coordinates) == 1  # 单源 = 1 元素 MultiPolygon


def test_merge_iso_aggregates_probe_batches_and_min_confidence() -> None:
    sq1 = [[(0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0), (0.0, 0.0)]]
    sq2 = [[(1.0, 1.0), (1.0, 3.0), (3.0, 3.0), (3.0, 1.0), (1.0, 1.0)]]
    comp1 = _comp(116.0, [_level(15, [sq1], confidence=1.0)], probe=10, batches=2)
    comp2 = _comp(116.0, [_level(15, [sq2], confidence=0.8)], probe=12, batches=3)
    merged = _merge_iso(
        [comp1, comp2],
        [15],
        (116.0, 39.98),
        [Coord(lng=116.0, lat=39.98, crs="bd09"), Coord(lng=116.0, lat=39.98, crs="bd09")],
    )
    assert merged.probe_count == 22
    assert merged.matrix_batches == 5
    assert merged.levels[0].confidence == 0.8  # min
    assert len(merged.origins) == 2
    assert len(merged.levels[0].coordinates) == 1  # 重叠并集融合为单多边形


def test_merge_iso_first_degraded_reason_wins() -> None:
    sq = [[(0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0), (0.0, 0.0)]]
    comp1 = _comp(116.0, [_level(15, [sq])], probe=10, batches=2, degraded="matrix:unavailable")
    comp2 = _comp(116.0, [_level(15, [sq])], probe=10, batches=2, degraded=None)
    merged = _merge_iso(
        [comp1, comp2],
        [15],
        (116.0, 39.98),
        [Coord(lng=116.0, lat=39.98, crs="bd09")],
    )
    assert merged.degraded_reason == "matrix:unavailable"
    assert merged.method == "straight-estimate"


def test_merge_iso_area_is_metric_not_square_degrees() -> None:
    """回归钉：并集面积必须回投米制（经纬度几何除 1e6 会得到 0，前端显示 0.00 km²）。"""
    origin = (116.316628, 39.981909)
    d = 1.0 / 111.2  # 约 1 km 的经纬度跨度
    sq = [
        [
            (origin[0] - d / 2, origin[1] - d / 2),
            (origin[0] - d / 2, origin[1] + d / 2),
            (origin[0] + d / 2, origin[1] + d / 2),
            (origin[0] + d / 2, origin[1] - d / 2),
            (origin[0] - d / 2, origin[1] - d / 2),
        ]
    ]
    comp = _comp(origin[0], [_level(15, [sq])], probe=10, batches=2)
    merged = _merge_iso([comp], [15], origin, [Coord(lng=origin[0], lat=origin[1], crs="bd09")])
    # 1 km × 1 km 方形，经向受 cos(lat) 压缩 → ≈ cos(lat) km²，而非平方度的 0
    assert merged.levels[0].area_km2 == pytest.approx(math.cos(math.radians(origin[1])), rel=0.05)
