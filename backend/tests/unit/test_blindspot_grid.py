"""盲区识别单元测试（纯函数，零 API）：栅格化 / cKDTree 最近距离 / 聚合平滑 / 严重度。

用小网格（2×2，500m 格）构造可手算的期望值；坐标经 offset_lnglat 往返，
顺带覆盖米空间 ↔ BD09 投影一致性。
"""

from __future__ import annotations

import pytest

from app.blindspot.grid import compute_blindspot
from app.core.config import Settings
from app.core.coords import local_delta_m, offset_lnglat
from app.mapapi.provider import BD09Point
from app.models.poi import PoiRecord

ORIGIN = BD09Point((116.316628, 39.981909))


def settings(**overrides) -> Settings:
    base = {
        "demo_mode": True,
        "blindspot_extent_m": 1000,  # 2×2 格，格心 (±250, ±250)
        "blindspot_cell_m": 500,
        "blindspot_threshold_m": 1000,
        "blindspot_buffer_m": 0.0,
        "blindspot_types": ["medical", "education", "shopping"],
    }
    return Settings(**(base | overrides))


def poi_at(dx_m: float, dy_m: float, uid: str) -> PoiRecord:
    lng, lat = offset_lnglat(ORIGIN[0], ORIGIN[1], dx_m, dy_m)
    return PoiRecord(uid=uid, name=uid, lng=lng, lat=lat)


def test_full_coverage_type_has_no_missing() -> None:
    result = compute_blindspot(
        ORIGIN,
        {"medical": [poi_at(0.0, 0.0, "m1")]},
        settings(blindspot_types=["medical"]),
        {"medical": "医疗"},
    )
    med = result.types[0]
    assert med.missing_cells == 0
    assert med.worst_distance_m is None
    assert med.polygons == []
    assert result.max_severity == 0
    assert result.severe_cells == 0


def test_empty_type_marks_whole_grid_missing() -> None:
    """该类完全无设施（编排层已确认检索成功）：全域缺失、worst 无意义（None）。"""
    result = compute_blindspot(
        ORIGIN, {"shopping": []}, settings(), {"shopping": "购物"}, types=["shopping"]
    )
    shop = result.types[0]
    assert shop.missing_cells == 4
    assert shop.worst_distance_m is None
    assert len(shop.polygons) == 1
    ring = shop.polygons[0][0]
    assert ring[0] == ring[-1], "GeoJSON 环必须闭合"
    for lng, lat in ring:
        dx, dy = local_delta_m(ORIGIN[0], ORIGIN[1], lng, lat)
        assert abs(dx) <= 500.0 + 1e-6 and abs(dy) <= 500.0 + 1e-6  # 投影往返浮点余量


@pytest.mark.regression
def test_types_injected_skips_unsearched_category() -> None:
    """编排层注入 types：未检索/检索失败的类目不参与判定（空数据 ≠ 全域缺失）。

    回归（BF-007）：types 曾固定取 settings.blindspot_types，用户取消勾选或
    类目检索降级时，空设施列表被栅格判成"全域缺失"——数据缺失被冒充成
    盲区事实（全图灰 + 复合盲区假警报）。
    """
    result = compute_blindspot(ORIGIN, {"shopping": []}, settings(), {"shopping": "购物"}, types=[])
    assert result.types == []
    assert result.max_severity == 0
    assert result.severe_cells == 0


def test_missing_region_polygon_and_worst_distance() -> None:
    """设施在北侧 (0, 900)：南行两格距 √(250²+1150²) ≈ 1177m > 1000 → 缺失。"""
    result = compute_blindspot(
        ORIGIN, {"medical": [poi_at(0.0, 900.0, "m1")]}, settings(), {"medical": "医疗"}
    )
    med = result.types[0]
    assert med.missing_cells == 2
    assert med.worst_distance_m == pytest.approx(1176.76, abs=1.0)
    # 南行两格共边相邻 → 聚合成单个 1000m × 500m 矩形
    assert len(med.polygons) == 1
    xs = [local_delta_m(ORIGIN[0], ORIGIN[1], lng, lat)[0] for lng, lat in med.polygons[0][0]]
    assert min(xs) == pytest.approx(-500.0, abs=1.0)
    assert max(xs) == pytest.approx(500.0, abs=1.0)


def test_diagonal_cells_merge_under_buffer() -> None:
    """对角相邻缺失格（仅角点接触）：buffer(50m) 融合为单一多边形（8 邻接语义）。

    设施在格心 (0,0)、threshold=300：四格心距 √2×250 ≈ 354m 全缺失。
    四格只共享 (0,0) 一个角点——unary_union 保持 4 片，buffer(50m) 封闭角点
    间隙后融合为 1 片，即"相邻同缺失类型栅格 8 邻接聚类"的几何实现。
    """
    facilities = {"medical": [poi_at(0.0, 0.0, "center")]}
    result = compute_blindspot(
        ORIGIN,
        facilities,
        settings(blindspot_threshold_m=300, blindspot_buffer_m=50.0),
        {"medical": "医疗"},
    )
    med = result.types[0]
    assert med.missing_cells == 4
    assert len(med.polygons) == 1


def test_severity_stacking_across_types() -> None:
    """medical 南行缺失 + education 全域缺失 → 南行 severity=2（复合盲区）。"""
    facilities = {
        "medical": [poi_at(0.0, 900.0, "m1")],
        "education": [],
        "shopping": [poi_at(0.0, 0.0, "s1")],
    }
    result = compute_blindspot(
        ORIGIN, facilities, settings(), {"medical": "医疗", "education": "教育", "shopping": "购物"}
    )
    assert [t.missing_cells for t in result.types] == [2, 4, 0]
    assert result.max_severity == 2
    assert result.severe_cells == 2


def test_grid_meta_fields() -> None:
    result = compute_blindspot(ORIGIN, {}, settings(), {})
    assert result.grid_side == 2
    assert result.extent_m == 1000
    assert result.cell_size_m == 500
    assert result.threshold_m == 1000
    assert result.origin.lng == ORIGIN[0] and result.origin.crs == "bd09"
