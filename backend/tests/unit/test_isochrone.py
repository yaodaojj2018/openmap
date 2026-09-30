"""等时圈域层单元测试：sampler 几何 / TimeField 分支 / fitter 拟合（纯函数）。"""

import math

import pytest

from app.core.coords import haversine_m
from app.isochrone.field import RayStatus, TimeField
from app.isochrone.fitter import catmull_rom_closed, fit_level
from app.isochrone.sampler import (
    Probe,
    direction_angles,
    midpoint_probe,
    offset_point,
    probe_points,
)
from app.models.isochrone import RouteLeg

ORIGIN = (116.316628, 39.981909)


# ---- sampler ----


def test_direction_angles_evenly_spaced() -> None:
    angles = direction_angles(16)
    assert len(angles) == 16
    assert angles[0] == 0.0
    spacing = math.pi * 2 / 16
    assert angles[1] == pytest.approx(spacing)


@pytest.mark.parametrize("theta_deg", [0, 45, 90, 157.5, 180, 270])
@pytest.mark.parametrize("radius", [300, 900, 1200])
def test_probe_radius_matches_haversine(theta_deg: float, radius: int) -> None:
    """回归测试：探针布设半径必须与 haversine 量得的直线距离一致。

    曾因椭球/球体常数混用（110540 vs 111194.9 m/deg）产生 ~0.6% 距离膨胀，
    导致回放模式在 cap 边界处大面积误判不可达。
    """
    lng, lat = offset_point(ORIGIN, math.radians(theta_deg), radius)
    assert haversine_m(ORIGIN[0], ORIGIN[1], lng, lat) == pytest.approx(radius, abs=2.0)


def test_probe_points_grid_size() -> None:
    probes = probe_points(ORIGIN, 16, [300, 600, 900, 1200])
    assert len(probes) == 64
    assert all(isinstance(p, Probe) for p in probes)


def test_midpoint_probe() -> None:
    mid = midpoint_probe(ORIGIN, 0.0, 900.0, 1200.0)
    assert mid.radius_m == pytest.approx(1050.0)
    assert haversine_m(ORIGIN[0], ORIGIN[1], mid.lng, mid.lat) == pytest.approx(1050.0, abs=2.0)


# ---- TimeField ----


def make_field(samples_by_theta: dict[float, dict[float, float | None]]) -> TimeField:
    field = TimeField(4)
    probes, legs = [], []
    for theta, samples in samples_by_theta.items():
        for radius, duration in samples.items():
            probes.append(Probe(theta, radius, 0.0, 0.0))
            legs.append(
                RouteLeg(distance_m=None if duration is None else radius, duration_s=duration)
            )
    field.ingest(probes, legs)
    return field


def test_ingest_length_mismatch_raises() -> None:
    field = TimeField(4)
    with pytest.raises(ValueError):
        field.ingest([Probe(0.0, 300, 0.0, 0.0)], [])


def test_level_radius_interp_within_samples() -> None:
    field = make_field({0.0: {300.0: 300.0, 600.0: 600.0, 900.0: 900.0}})
    rr = field.level_radius(450.0)[0]
    assert rr.radius_m == pytest.approx(450.0)
    assert rr.status is RayStatus.OK


def test_level_radius_origin_to_first_sample() -> None:
    field = make_field({0.0: {300.0: 300.0, 600.0: 600.0}})
    rr = field.level_radius(150.0)[0]
    assert rr.radius_m == pytest.approx(150.0)
    assert rr.status is RayStatus.OK


def test_level_radius_blocked_takes_far_reachable() -> None:
    """600m 探针不可达 ⇒ 级别超出最远可达时间时保守取 300m（河流/围墙语义）。"""
    field = make_field({0.0: {300.0: 300.0, 600.0: None}})
    rr = field.level_radius(400.0)[0]
    assert rr.radius_m == pytest.approx(300.0)
    assert rr.status is RayStatus.BLOCKED


def test_level_radius_extrapolated_capped_1_3x() -> None:
    """无阻挡但采样不足：按时间比例外推，封顶最远点 1.3 倍。"""
    field = make_field({0.0: {300.0: 300.0}})
    rr = field.level_radius(600.0)[0]
    assert rr.radius_m == pytest.approx(390.0)  # min(300*600/300, 300*1.3)
    assert rr.status is RayStatus.EXTRAPOLATED


def test_level_radius_empty_ray_blocked_zero() -> None:
    field = make_field({0.0: {300.0: 300.0}})  # 其余 3 个方向无样本
    radii = field.level_radius(600.0)
    assert radii[1].status is RayStatus.BLOCKED
    assert radii[1].radius_m == 0.0


def test_refine_brackets_and_bisection_convergence() -> None:
    field = make_field({0.0: {300.0: 300.0, 600.0: 600.0, 900.0: 900.0, 1200.0: 1200.0}})
    assert field.refine_brackets(950.0) == [(0.0, 900.0, 1200.0)]
    # 二分中点 1050m/1050s 落在级别之外 → 区间从下端收窄为 (900, 1050)
    field.ingest([Probe(0.0, 1050.0, 0.0, 0.0)], [RouteLeg(distance_m=1050, duration_s=1050)])
    assert field.refine_brackets(950.0) == [(0.0, 900.0, 1050.0)]


def test_refine_brackets_skips_narrow_and_blocked() -> None:
    # 穿越区间宽 ≤ min_gap：不再二分（注意原点对 (0,600) 不穿越 625s）
    field = make_field({0.0: {600.0: 600.0, 650.0: 650.0}})
    assert field.refine_brackets(625.0, min_gap_m=50.0) == []
    # 阻挡方向级别超出最远可达：不参与二分
    blocked = make_field({0.0: {300.0: 300.0, 600.0: None}})
    assert blocked.refine_brackets(950.0) == []


# ---- fitter ----


def test_catmull_rom_vertex_count() -> None:
    pts = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]
    assert len(catmull_rom_closed(pts, 5)) == 20


def test_fit_level_round_shape() -> None:
    """均匀半径 → 面积接近圆、GeoJSON 环闭合、全实测置信度 1.0。"""
    radii = _radii_all(8, 500.0)
    rings, area_km2, confidence = fit_level(ORIGIN, radii)
    assert rings[0][0] == rings[0][-1]
    assert len(rings[0]) == 8 * 10 + 1  # 8 段 × 10 采样 + 闭合重复顶点
    assert area_km2 == pytest.approx(math.pi * 0.5**2, rel=0.15)
    assert confidence == 1.0


def test_fit_level_confidence_penalizes_extrapolation() -> None:
    """2/8 方向外推：0.75 × (1 − 0.5×0.25) = 0.656。"""
    radii = _radii_all(8, 500.0)
    radii[0] = _replace_status(radii[0], RayStatus.EXTRAPOLATED)
    radii[1] = _replace_status(radii[1], RayStatus.EXTRAPOLATED)
    _, _, confidence = fit_level(ORIGIN, radii)
    assert confidence == pytest.approx(0.656, abs=1e-3)


def _radii_all(n: int, radius: float) -> list:
    from app.isochrone.field import RayRadius

    return [
        RayRadius(theta=2 * math.pi * i / n, radius_m=radius, status=RayStatus.OK) for i in range(n)
    ]


def _replace_status(ray: object, status: RayStatus) -> object:
    from dataclasses import replace

    return replace(ray, status=status)  # type: ignore[arg-type]
