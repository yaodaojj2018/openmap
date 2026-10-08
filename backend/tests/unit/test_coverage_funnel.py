"""覆盖判定三级漏斗单元测试（纯函数，无 IO）。

漏斗语义（docs/02 §3.2）：圈外几何定论 / 圈内远离阈值带插值定论 /
边缘带与不可估算点收集待矩阵精判；精判覆写为实测口径。
"""

from __future__ import annotations

import pytest

from app.core.coords import local_delta_m, offset_lnglat
from app.coverage.funnel import (
    build_polygon,
    classify_facilities,
    merge_matrix_verdicts,
    merge_walking_verdicts,
    summarize_categories,
)
from app.mapapi.provider import BD09Point
from app.models.coverage import CoverageMethod
from app.models.isochrone import RouteLeg
from app.models.poi import PoiRecord

ORIGIN = BD09Point((116.316628, 39.981909))
THRESHOLD_S = 15 * 60.0
BAND_S = 2 * 60.0
FIELD_CONF = 0.8


def poi_at(dx_m: float, dy_m: float, uid: str = "p", category: str = "medical") -> PoiRecord:
    lng, lat = offset_lnglat(ORIGIN[0], ORIGIN[1], dx_m, dy_m)
    return PoiRecord(uid=uid, name=uid, lng=lng, lat=lat, category=category)


def square_polygon(half_m: float = 700.0):
    """以 ORIGIN 为中心的方形环（米空间布点，真正的方形），模拟最大级等时圈。"""
    corners = [
        offset_lnglat(ORIGIN[0], ORIGIN[1], -half_m, -half_m),
        offset_lnglat(ORIGIN[0], ORIGIN[1], half_m, -half_m),
        offset_lnglat(ORIGIN[0], ORIGIN[1], half_m, half_m),
        offset_lnglat(ORIGIN[0], ORIGIN[1], -half_m, half_m),
    ]
    ring = [[lng, lat] for lng, lat in [*corners, corners[0]]]
    return build_polygon([ring])


def classify(records, polygon, estimator):
    return classify_facilities(
        records,
        polygon,
        estimator,
        THRESHOLD_S,
        THRESHOLD_S - BAND_S,
        THRESHOLD_S + BAND_S,
        FIELD_CONF,
    )


def _dx_of(lng: float) -> float:
    return local_delta_m(ORIGIN[0], ORIGIN[1], lng, ORIGIN[1])[0]


def test_level1_outside_polygon_settled_by_geometry() -> None:
    verdicts, edge = classify([poi_at(2000.0, 0.0, "far")], square_polygon(), lambda *_: 999.0)
    assert edge == []
    assert verdicts[0].method is CoverageMethod.POLYGON
    assert verdicts[0].in_circle is False
    assert verdicts[0].est_walk_time_min is None
    assert verdicts[0].confidence == 1.0


def test_level2_far_from_band_settled_by_field() -> None:
    records = [poi_at(200.0, 0.0, "near"), poi_at(600.0, 0.0, "mid")]
    # 近点 6min（圈内）、远点 30min（圈外），均远离 [13,17] 边缘带
    verdicts, edge = classify(
        records, square_polygon(), lambda lng, _: 360.0 if _dx_of(lng) < 400 else 1800.0
    )
    assert edge == []
    assert [(v.in_circle, v.method) for v in verdicts] == [
        (True, CoverageMethod.FIELD),
        (False, CoverageMethod.FIELD),
    ]
    assert verdicts[0].est_walk_time_min == pytest.approx(6.0)
    assert verdicts[0].confidence == FIELD_CONF


def test_level3_edge_band_collected_sorted_by_closeness() -> None:
    """边缘带设施全部收集精判，按 |t̂ - T| 升序（配额优先花在最贴近阈值处）。"""
    records = [poi_at(100.0, 0.0, "a"), poi_at(200.0, 0.0, "b"), poi_at(300.0, 0.0, "c")]
    minutes = {100.0: 14.0, 200.0: 15.8, 300.0: 13.1}  # dx → t̂（分钟）

    def est(lng: float, _: float) -> float:
        dx = min(minutes, key=lambda anchor: abs(_dx_of(lng) - anchor))
        return minutes[dx] * 60

    verdicts, edge = classify(records, square_polygon(), est)
    assert sorted(edge) == [0, 1, 2]
    assert edge[0] == 1  # b: |15.8-15| 最小，优先精判
    assert all(v.method is CoverageMethod.FIELD for v in verdicts)  # 精判前保留插值口径


@pytest.mark.regression
def test_unestimatable_point_conservative_and_verifies_first() -> None:
    """阻挡方向估算不可用：保守判圈外（不冒充可达），且排精判队列首位。

    回归（BF-008）：几何先验兜底曾把该类设施记为 in_circle=True——精判配额
    截断或矩阵降级时它们不再实测，reachable 与评分被系统性抬高。
    """
    records = [poi_at(100.0, 0.0, "blocked"), poi_at(200.0, 0.0, "edge")]

    def est(lng: float, _: float) -> float | None:
        return None if _dx_of(lng) < 150 else 15.5 * 60

    verdicts, edge = classify(records, square_polygon(), est)
    assert edge[0] == 0
    assert verdicts[0].in_circle is False  # 未获实测前保守不计入可达
    assert verdicts[0].est_walk_time_min is None
    assert verdicts[0].confidence == FIELD_CONF / 2


def test_merge_matrix_overrides_edge_verdicts() -> None:
    records = [poi_at(100.0, 0.0, "ok"), poi_at(200.0, 0.0, "slow"), poi_at(300.0, 0.0, "cut")]
    verdicts, edge = classify(records, square_polygon(), lambda *_: 15.2 * 60)
    assert len(edge) == 3
    legs = [
        RouteLeg(distance_m=800.0, duration_s=700.0),  # 实测 11.7min → 圈内
        RouteLeg(distance_m=1500.0, duration_s=1300.0),  # 21.7min → 圈外
        RouteLeg(distance_m=None, duration_s=None),  # 实测不可达 → 圈外，定论
    ]
    merged = merge_matrix_verdicts(verdicts, edge, legs, THRESHOLD_S)
    assert [(v.in_circle, v.method, v.confidence) for v in merged] == [
        (True, CoverageMethod.MATRIX, 1.0),
        (False, CoverageMethod.MATRIX, 1.0),
        (False, CoverageMethod.MATRIX, 1.0),
    ]
    assert merged[0].est_walk_time_min == pytest.approx(700.0 / 60)
    assert merged[2].est_walk_time_min is None


def test_summarize_categories_score_formula() -> None:
    """score = 100×(0.6×可达率 + 0.4×min(1, 可达/5))；空类目显式 0 分。"""
    verdicts, _ = classify(
        [poi_at(i * 50.0, 0.0, f"m{i}") for i in range(4)],
        square_polygon(),
        lambda *_: 300.0,  # 全部 5min 圈内
    )
    # 4 可达 / 4 总数：0.6×1 + 0.4×(4/5) = 0.92 → 92
    stats = summarize_categories(verdicts, {"medical": "医疗"}, sufficient_count=5)
    assert stats[0].total == 4
    assert stats[0].reachable == 4
    assert stats[0].coverage_ratio == 1.0
    assert stats[0].avg_walk_time_min == pytest.approx(5.0)
    assert stats[0].score == 92.0
    # 空类目：显式 0 分而非报错（docs/02 §3.4 降级链）
    empty = summarize_categories([], {"shopping": "购物"}, sufficient_count=5)
    assert empty[0].total == 0
    assert empty[0].score == 0.0
    assert empty[0].avg_walk_time_min is None


def test_build_polygon_empty_rings_returns_none() -> None:
    assert build_polygon([]) is None
    assert build_polygon([[]]) is None


def test_merge_matrix_row_count_mismatch_raises() -> None:
    """leg 数与精判清单不符 → ValueError：编排层捕获后降级，绝不静默错配。"""
    verdicts, edge = classify([poi_at(100.0, 0.0, "a")], square_polygon(), lambda *_: 15.2 * 60)
    with pytest.raises(ValueError):
        merge_matrix_verdicts(verdicts, edge, [], THRESHOLD_S)


def test_merge_walking_keeps_interpolation_for_failed_legs() -> None:
    """降级链第二级（docs/02 §3.4）：实测条目按 WALKING 口径覆写；
    None（规划失败/预算拦截）保留插值判定，不冒充实测。"""
    records = [poi_at(100.0, 0.0, "ok"), poi_at(200.0, 0.0, "lost")]
    verdicts, edge = classify(records, square_polygon(), lambda *_: 15.2 * 60)
    assert sorted(edge) == [0, 1]
    merged, measured = merge_walking_verdicts(
        verdicts,
        edge,
        [RouteLeg(distance_m=800.0, duration_s=700.0), None],
        THRESHOLD_S,
    )
    assert measured == 1
    assert (merged[0].in_circle, merged[0].method, merged[0].confidence) == (
        True,
        CoverageMethod.WALKING,
        1.0,
    )
    assert merged[1].method is CoverageMethod.FIELD, "失败条目保留插值口径"
    assert merged[1].confidence == FIELD_CONF


def test_merge_walking_unreachable_leg_settled_outside() -> None:
    """逐条实测不可达：定论圈外（method=WALKING，与矩阵口径同为确定）。"""
    verdicts, edge = classify([poi_at(100.0, 0.0, "a")], square_polygon(), lambda *_: 15.2 * 60)
    merged, measured = merge_walking_verdicts(
        verdicts, edge, [RouteLeg(distance_m=None, duration_s=None)], THRESHOLD_S
    )
    assert measured == 1
    assert merged[0].in_circle is False
    assert merged[0].est_walk_time_min is None
    assert merged[0].method is CoverageMethod.WALKING
