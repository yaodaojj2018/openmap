"""等时圈第二法单元测试：网格插值 / marching squares / 双法比对与加密选点（纯函数）。

合成场一律用"径向线性"（秒数 = 半径 / 2）——它的等值面是正圆，解析解已知，
能在不依赖上游 API 的前提下把 marching squares、polygonize、radial_distance
三者的口径钉死。
"""

import math
from dataclasses import replace

import numpy as np
import pytest
from shapely.geometry import Polygon

from app.isochrone.field import RayRadius, RayStatus, TimeField
from app.isochrone.fitter import fit_level
from app.isochrone.grid import (
    _AMBIGUOUS,
    _EDGE_PAIRS,
    GridField,
    build_grid,
    contour_polygons,
    extract_segments,
    radial_distance,
)
from app.isochrone.reconcile import densify_probes, enclave_probes, grid_consistency
from app.isochrone.sampler import offset_point, probe_points
from app.models.isochrone import RouteLeg

ORIGIN = (116.316628, 39.981909)
STEP_M = 100.0
EXTENT_M = 2400.0
TOLERANCE_M = 100.0

# 格角位（与 grid 内部约定一致）：0=bl(i,j) 1=br(i+1,j) 2=tr(i+1,j+1) 3=tl(i,j+1)
_EDGE_CORNERS = {0: (0, 1), 1: (1, 2), 2: (3, 2), 3: (0, 3)}


def _inside(code: int, corner_bit: int) -> bool:
    return bool(code & (1 << corner_bit))


# ---- marching squares 查表自洽（曾把 case 12 写成 case 9 的镜像）----


def test_every_edge_pair_connects_a_crossed_edge() -> None:
    """每个 case 用到的两条边必须"一内一外"。

    若表里挂上了同侧的两条边，线性插值系数 (level−v_a)/(v_b−v_a) 会跑出 [0,1]，
    等值点被甩到网格之外（本地开发时确曾把 case 12 写成 case 9 的镜像，
    实测等值点坐标到 7.5km，网格半径仅 2.4km，等值线直接不闭合）——
    查表类笔误无法从类型或调用点看出，只能靠逐 case 断言拦住。
    """
    for code in range(16):
        if code in (0, 15):
            continue
        variants = _AMBIGUOUS[code] if code in _AMBIGUOUS else (_EDGE_PAIRS[code],)
        for pairs in variants:
            for edge_a, edge_b in pairs:
                for edge in (edge_a, edge_b):
                    bit_a, bit_b = _EDGE_CORNERS[edge]
                    assert _inside(code, bit_a) != _inside(code, bit_b), (
                        f"case {code} 用了非跨越边 {edge}（两侧同在内/外）"
                    )


def test_every_case_produces_expected_segment_count() -> None:
    """非退化 case 各 1 条线段（对角模糊 2 条）；全内/全外的 0、15 不进表。"""
    assert set(_EDGE_PAIRS) == {1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14}
    assert all(len(pairs) == 1 for pairs in _EDGE_PAIRS.values())
    assert set(_AMBIGUOUS) == {5, 10}
    assert 0 not in _EDGE_PAIRS and 15 not in _EDGE_PAIRS
    for code in (5, 10):
        inside, outside = _AMBIGUOUS[code]
        assert len(inside) == len(outside) == 2


# ---- build_grid ----


def _radial_field(directions: int = 16, rings: list[int] | None = None) -> TimeField:
    """径向线性合成场：秒数 = 半径 / 2（等时面为正圆，300s 圈半径 600m）。"""
    rings = rings if rings is not None else [300, 600, 900, 1200]
    field = TimeField(directions)
    probes = probe_points(ORIGIN, directions, rings)
    field.ingest(
        probes, [RouteLeg(distance_m=p.radius_m, duration_s=p.radius_m / 2) for p in probes]
    )
    return field


def _radial_grid() -> GridField:
    points, times = _radial_field().scatter(extrapolate_to_m=EXTENT_M)
    grid = build_grid(points, times, STEP_M, EXTENT_M)
    assert grid is not None
    return grid


def test_build_grid_degenerate_inputs_return_none() -> None:
    """样本不足/共线/步长非正 → None，调用方退回单法，不虚构几何。"""
    assert build_grid([(0.0, 0.0), (1.0, 1.0)], [1.0, 2.0], STEP_M, EXTENT_M) is None
    assert (
        build_grid([(0.0, 0.0), (100.0, 100.0), (200.0, 200.0)], [1.0, 2.0, 3.0], 100.0, 600.0)
        is None
    )
    pts = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0)]
    assert build_grid(pts, [1.0, 2.0, 3.0], 0.0, EXTENT_M) is None
    assert build_grid(pts, [1.0, 2.0, 3.0], STEP_M, 0.0) is None


def test_build_grid_fills_hull_outside_with_nearest() -> None:
    """凸包外格点由 nearest 填补：网格全有限，边缘不留 NaN 断口。"""
    grid = _radial_grid()
    assert grid.values.shape == (49, 49)  # (-2400, 2400] 步长 100
    assert bool((grid.values > 0).all())


def test_contour_of_radial_field_is_a_circle() -> None:
    """径向线性场的 300s 等值线 ≈ 半径 600m 的圆；面积 ≈ π·0.6² km²。"""
    grid = _radial_grid()
    polys = contour_polygons(grid, 300.0)
    assert len(polys) == 1, "正圆等值线必须闭合为单一面"
    main = polys[0]
    assert main.area / 1e6 == pytest.approx(math.pi * 0.6**2, rel=0.15)
    for theta in (0.0, math.pi / 4, math.pi / 2, math.pi, 3 * math.pi / 2):
        radius = radial_distance(main, theta, EXTENT_M)
        assert radius is not None
        assert radius == pytest.approx(600.0, abs=60.0), f"θ={theta} 处等值线半径偏离"


def test_contour_returns_empty_when_level_beyond_grid() -> None:
    """级别超出场上界（圈落在网格之外）：返回空，调用方退回样条单法。"""
    assert contour_polygons(_radial_grid(), 5000.0) == []


def test_extract_segments_skips_non_finite_cells() -> None:
    """含无数值格点不产出线段（不跨越无数据区插值）。

    NaN 落在等值线上（300s 圈半径 600m → 索引 (j=24, i=30)），否则该格本就无线段，
    减不减少都恒等，测不出行为。
    """
    grid = _radial_grid()
    broken = replace(grid, values=grid.values.copy())
    broken.values[24, 30] = np.nan
    finite = len(extract_segments(grid, 300.0))
    assert finite > 0
    assert len(extract_segments(broken, 300.0)) < finite


def test_radial_distance_takes_farthest_intersection() -> None:
    """有内环的面取最外交点：内环不缩短外边界半径。"""
    box = Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)])
    assert radial_distance(box, 0.0, 1000.0) == pytest.approx(100.0)
    assert radial_distance(box, math.pi / 4, 1000.0) == pytest.approx(math.sqrt(2) * 100, rel=1e-6)
    hole = Polygon(
        [(-100, -100), (100, -100), (100, 100), (-100, 100)],
        [[(-50, -50), (50, -50), (50, 50), (-50, 50)]],
    )
    assert radial_distance(hole, 0.0, 1000.0) == pytest.approx(100.0)


def test_radial_distance_without_intersection_is_none() -> None:
    box = Polygon([(500, -100), (600, -100), (600, 100), (500, 100)])
    assert radial_distance(box, math.pi, 1000.0) is None  # 射线朝西，框在东侧


# ---- reconcile：双法一致性 ----


def _radii(pairs: list[tuple[float, float]], status: RayStatus = RayStatus.OK) -> list[RayRadius]:
    return [RayRadius(theta=theta, radius_m=r, status=status) for theta, r in pairs]


def _circle(radius: float, segs: int = 64) -> list[Polygon]:
    pts = [
        (radius * math.cos(2 * math.pi * i / segs), radius * math.sin(2 * math.pi * i / segs))
        for i in range(segs)
    ]
    return [Polygon(pts)]


def test_grid_consistency_full_and_partial_agreement() -> None:
    radii = _radii([(2 * math.pi * i / 8, 500.0) for i in range(8)])
    assert grid_consistency(radii, _circle(500.0), TOLERANCE_M, EXTENT_M) == 1.0
    # 半径 620 与等值线 500 差 120m > 容差：8 方向中 2 个背离 → 0.75
    radii[0] = RayRadius(0.0, 620.0, RayStatus.OK)
    radii[1] = RayRadius(2 * math.pi / 8, 620.0, RayStatus.OK)
    assert grid_consistency(radii, _circle(500.0), TOLERANCE_M, EXTENT_M) == 0.75


def test_grid_consistency_none_without_usable_contour() -> None:
    """第二法不可用时返回 None——绝不用"默认一致"冒充校验通过。"""
    radii = _radii([(0.0, 500.0)])
    assert grid_consistency(radii, [], TOLERANCE_M, EXTENT_M) is None
    # 等值线不含该方位（射线打不到边界）同样判不可用
    box = Polygon([(500, -100), (600, -100), (600, 100), (500, 100)])
    assert grid_consistency(_radii([(math.pi, 500.0)]), [box], TOLERANCE_M, EXTENT_M) is None


def test_grid_consistency_skips_blocked_directions() -> None:
    """阻挡方向是确定信息（第二法结构上无法表达），不计入一致性分母。"""
    radii = _radii([(0.0, 500.0), (math.pi / 2, 300.0)])
    radii[1] = RayRadius(math.pi / 2, 300.0, RayStatus.BLOCKED)
    assert grid_consistency(radii, _circle(500.0), TOLERANCE_M, EXTENT_M) == 1.0


def test_grid_consistency_all_blocked_is_none() -> None:
    radii = _radii([(0.0, 300.0)], RayStatus.BLOCKED)
    assert grid_consistency(radii, _circle(500.0), TOLERANCE_M, EXTENT_M) is None


def test_grid_consistency_uses_primary_region_only() -> None:
    """碎片连通块（稀疏采样假结构）不参与比对——否则 radial_distance 的取最大值
    会被网格边缘的孤岛抬到远处，制造假偏差。"""
    radii = _radii([(0.0, 500.0), (math.pi, 500.0)])
    main = _circle(500.0)[0]
    far = Polygon([(2100, -50), (2200, -50), (2200, 50), (2100, 50)])
    assert grid_consistency(radii, [far, main], TOLERANCE_M, EXTENT_M) == 1.0


# ---- reconcile：加密与飞地选点 ----


def test_densify_probes_hit_contour_radius_on_deviating_bearings() -> None:
    """偏差方位按等值线主张的半径补采；一致方位与阻挡方位不补。"""
    radii = _radii([(2 * math.pi * i / 8, 500.0) for i in range(8)])
    radii[0] = RayRadius(0.0, 700.0, RayStatus.OK)  # 偏差 200m
    radii[1] = RayRadius(2 * math.pi / 8, 300.0, RayStatus.BLOCKED)  # 阻挡，不补
    probes = densify_probes(radii, _circle(500.0), ORIGIN, TOLERANCE_M, EXTENT_M, 10)
    assert [p.theta for p in probes] == [0.0]
    assert probes[0].radius_m == pytest.approx(500.0, abs=1.0)
    # 探针坐标必须落在该方位、该半径上（供矩阵实测）
    lng, lat = offset_point(ORIGIN, 0.0, 500.0)
    assert (probes[0].lng, probes[0].lat) == pytest.approx((lng, lat))


def test_densify_probes_respect_limit_and_order() -> None:
    """超限时按方位角升序取前 limit 个（同一时间场可复现）。"""
    radii = _radii([(2 * math.pi * i / 8, 900.0) for i in range(8)])
    probes = densify_probes(radii, _circle(500.0), ORIGIN, TOLERANCE_M, EXTENT_M, 3)
    assert [p.theta for p in probes] == [0.0, 2 * math.pi / 8, 4 * math.pi / 8]


def test_densify_probes_none_when_no_contour_or_limit() -> None:
    radii = _radii([(0.0, 700.0)])
    assert densify_probes(radii, [], ORIGIN, TOLERANCE_M, EXTENT_M, 10) == []
    assert densify_probes(radii, _circle(500.0), ORIGIN, TOLERANCE_M, EXTENT_M, 0) == []


def test_enclave_probes_detect_adjacent_radius_jump() -> None:
    """相邻方向临界半径比 ≥ 2 → 扇区中点按两侧半径各补 1 探针。"""
    radii = _radii([(0.0, 200.0), (math.pi / 2, 1000.0)])
    probes = enclave_probes(radii, ORIGIN, 2.0, EXTENT_M, 10)
    assert [(p.theta, p.radius_m) for p in probes] == pytest.approx(
        [(math.pi / 4, 200.0), (math.pi / 4, 1000.0)]
    )


def test_enclave_probes_ignore_smooth_field_and_blocked() -> None:
    """半径平滑过渡不补采；阻挡方向是确定阻挡，不判飞地。"""
    smooth = _radii([(0.0, 500.0), (math.pi / 2, 600.0), (math.pi, 550.0)])
    assert enclave_probes(smooth, ORIGIN, 2.0, EXTENT_M, 10) == []
    blocked = [
        RayRadius(0.0, 300.0, RayStatus.BLOCKED),
        RayRadius(math.pi / 2, 1200.0, RayStatus.OK),
    ]
    assert enclave_probes(blocked, ORIGIN, 2.0, EXTENT_M, 10) == []
    assert (
        enclave_probes(_radii([(0.0, 0.0), (math.pi / 2, 1000.0)]), ORIGIN, 2.0, EXTENT_M, 10) == []
    )


def test_enclave_probes_wrap_across_zero_bearing() -> None:
    """0 方位角两侧的相邻对（337.5° ↔ 0°）取 348.75° 为扇区中点，不绕到 168.75°。"""
    radii = _radii([(0.0, 1000.0), (7 * math.pi / 4, 200.0)])
    probes = enclave_probes(radii, ORIGIN, 2.0, EXTENT_M, 10)
    assert len(probes) == 2
    assert probes[0].theta == pytest.approx(2 * math.pi - math.pi / 8)


# ---- fitter：一致性折算置信度 ----


def test_fit_level_consistency_scales_confidence() -> None:
    radii = _radii([(2 * math.pi * i / 8, 500.0) for i in range(8)])
    _, _, baseline = fit_level(ORIGIN, radii)
    _, _, aligned = fit_level(ORIGIN, radii, consistency=1.0)
    _, _, divergent = fit_level(ORIGIN, radii, consistency=0.0)
    assert baseline == 1.0
    assert aligned == pytest.approx(1.0)  # 两法完全一致：不罚
    assert divergent == pytest.approx(0.5)  # 完全背离：折半
