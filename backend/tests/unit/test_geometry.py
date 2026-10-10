"""core/geometry 单元测试：GeoJSON 环组 ↔ shapely 多边形往返、洞与 MultiPolygon。"""

import math

import pytest

from app.core.geometry import geometry_to_rings, local_area_km2, rings_to_geometry


def test_rings_to_geometry_empty_returns_none() -> None:
    assert rings_to_geometry([]) is None
    assert rings_to_geometry([[]]) is None


def test_polygon_roundtrip_with_hole() -> None:
    outer = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]
    hole = [(2.0, 2.0), (2.0, 4.0), (4.0, 4.0), (4.0, 2.0), (2.0, 2.0)]
    geom = rings_to_geometry([outer, hole])
    assert geom is not None
    assert geom.geom_type == "Polygon"

    rings = geometry_to_rings(geom)
    assert len(rings) == 1  # 单多边形
    assert len(rings[0]) == 2  # 外环 + 内环
    # 环坐标取整 6 位（与 fitter 口径一致）
    assert rings[0][0][0] == [0.0, 0.0]


def test_multipolygon_roundtrip_disjoint() -> None:
    sq1 = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    sq2 = [(10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (11.0, 10.0), (10.0, 10.0)]
    geom = rings_to_geometry([sq1])
    assert geom is not None
    other = rings_to_geometry([sq2])
    assert other is not None

    from shapely import unary_union

    union = unary_union([geom, other])
    rings = geometry_to_rings(union)
    assert union.geom_type == "MultiPolygon"
    assert len(rings) == 2


def test_local_area_km2_deg_vs_metric() -> None:
    """经纬度几何必须回投米制：平方度直接除 1e6 会得到 0（面积显示 0.00 的根因）。"""
    origin = (116.316628, 39.981909)
    # 以 origin 为中心、约 1 km 见方的经纬度矩形（1°≈111.2 km，1 km≈0.009°）
    d = 1.0 / 111.2
    square = rings_to_geometry(
        [
            [
                (origin[0] - d / 2, origin[1] - d / 2),
                (origin[0] - d / 2, origin[1] + d / 2),
                (origin[0] + d / 2, origin[1] + d / 2),
                (origin[0] + d / 2, origin[1] - d / 2),
                (origin[0] - d / 2, origin[1] - d / 2),
            ]
        ]
    )
    assert square is not None
    # 回归钉：原始 bug 用 square.area / 1e6（平方度）≈ 0
    assert square.area / 1e6 < 1e-3
    # 修复后：米制投影。方形 1 km（纬向）× 1 km（经向），经向受 cos(lat) 压缩，
    # 故面积 ≈ 1 km² × cos(lat) ≈ 0.766 km²（而非 0）
    expected = math.cos(math.radians(origin[1]))
    assert local_area_km2(square, origin) == pytest.approx(expected, rel=0.02)
