"""等时圈拟合：射线半径 → Catmull-Rom 闭合样条多边形（纯几何）。

在米制局部平面拟合与计算面积（避免经纬度两轴失真），顶点最后经
 sampler 的同一套线性投影映射回 BD09 经纬度输出 GeoJSON。
"""

from __future__ import annotations

import math

from shapely import make_valid
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from app.isochrone.field import RayRadius, RayStatus
from app.isochrone.sampler import M_PER_DEG_LAT, M_PER_DEG_LNG_AT_EQUATOR
from app.mapapi.provider import BD09Point

# GeoJSON 坐标小数位：1e-6 度 ≈ 0.1m，远小于采样精度
_COORD_PRECISION = 6


def catmull_rom_closed(
    pts: list[tuple[float, float]], samples_per_seg: int
) -> list[tuple[float, float]]:
    """闭合 Catmull-Rom 样条（过控制点，段间 C1 连续）。

    段 P1→P2：q(t) = 0.5·(2P1 + (P2-P0)t + (2P0-5P1+4P2-P3)t² + (3P1-P0-3P2+P3)t³)
    """
    n = len(pts)
    if n < 3:
        return pts
    out: list[tuple[float, float]] = []
    for i in range(n):
        p0 = pts[(i - 1) % n]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n]
        for j in range(samples_per_seg):
            t = j / samples_per_seg
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                2 * p1[0]
                + (p2[0] - p0[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (3 * p1[0] - p0[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (p2[1] - p0[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (3 * p1[1] - p0[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    return out


def fit_level(
    origin: BD09Point,
    radii: list[RayRadius],
    samples_per_seg: int = 10,
) -> tuple[list[list[list[float]]], float, float]:
    """单级等时圈拟合 → (GeoJSON rings, 面积 km², 置信度)。

    置信度 = 实测方向占比 × (1 − 0.5×外推占比)：全实测 = 1.0，全外推 = 0.5；
    阻挡按实测计——凹陷是确定信息而非不确定度。
    """
    ordered = sorted(radii, key=lambda rr: rr.theta)
    pts_m = [(rr.radius_m * math.cos(rr.theta), rr.radius_m * math.sin(rr.theta)) for rr in ordered]
    polygon = _valid_polygon(Polygon(catmull_rom_closed(pts_m, samples_per_seg)))

    kx = M_PER_DEG_LNG_AT_EQUATOR * math.cos(math.radians(origin[1]))
    ky = M_PER_DEG_LAT
    rings = [
        [
            [
                round(origin[0] + x / kx, _COORD_PRECISION),
                round(origin[1] + y / ky, _COORD_PRECISION),
            ]
            for x, y in ring
        ]
        for ring in (polygon.exterior.coords, *(hole.coords for hole in polygon.interiors))
    ]
    area_km2 = round(polygon.area / 1e6, 4)
    return rings, area_km2, _confidence(ordered)


def _valid_polygon(geom: BaseGeometry) -> Polygon:
    """make_valid 后确保拿到单一 Polygon；极端自交残留时取面积最大片。"""
    geom = make_valid(geom)
    if isinstance(geom, Polygon):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
    if not parts:  # pragma: no cover - 星形正序半径理论上不会触发
        raise ValueError("等时圈多边形退化：无法构造有效面")
    return max(parts, key=lambda g: g.area)


def _confidence(radii: list[RayRadius]) -> float:
    n = len(radii)
    if n == 0:
        return 0.0
    extrapolated = sum(1 for rr in radii if rr.status is RayStatus.EXTRAPOLATED)
    measured = n - extrapolated
    return round((measured / n) * (1 - 0.5 * extrapolated / n), 3)
