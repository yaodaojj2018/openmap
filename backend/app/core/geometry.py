"""几何工具：GeoJSON 环组 ↔ shapely 多边形的跨域转换（纯函数，无 IO）。

isochrone 与 coverage 属同层域层（铁律 #1 禁止横向 import）。多源并集等时圈需要
把各源的 GeoJSON 多边形重建为 shapely、unary_union 成 MultiPolygon、再序列化回
GeoJSON——该转换放 core 供两侧复用，避免重复实现与同层 import。
"""

from __future__ import annotations

import math

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.core.coords import M_PER_DEG

GeoJsonPolygon = list[list[list[float]]]
GeoJsonMultiPolygon = list[list[list[list[float]]]]


def rings_to_geometry(rings: GeoJsonPolygon) -> BaseGeometry | None:
    """GeoJSON 环组（外环 + 内环）→ shapely Polygon；空环返回 None。"""
    if not rings or not rings[0]:
        return None
    return Polygon(shell=rings[0], holes=rings[1:] or None)


def geometry_to_rings(geom: BaseGeometry) -> GeoJsonMultiPolygon:
    """shapely 几何（Polygon / MultiPolygon）→ GeoJSON MultiPolygon 环组。

    每个多边形 = [外环, 内环...]；环坐标取整 6 位（≈0.1m，与 fitter 口径一致）。
    """
    polygons = [g for g in getattr(geom, "geoms", (geom,)) if isinstance(g, Polygon)]
    rings: GeoJsonMultiPolygon = []
    for poly in polygons:
        rings.append(
            [
                [[round(x, 6), round(y, 6)] for x, y in ring]
                for ring in (poly.exterior.coords, *(h.coords for h in poly.interiors))
            ]
        )
    return rings


def local_area_km2(geom: BaseGeometry, origin: tuple[float, float]) -> float:
    """经纬度几何 → 面积 km²：回投以 origin 为原点的米制局部平面。

    多源并集（unary_union 输出）顶点仍是 BD09 经纬度，shapely 直接求面积
    单位是「平方度」——~1 km 圈仅 1e-4 平方度量级，除 1e6 后四舍五入为 0，
    正是等时圈面积显示 0.00 km² 的根因。必须先投影回米制（与
    coords.local_delta_m / fitter.rings_from_polygon 同一投影族：cos 纬度
    纬向收缩，1.5 km 量级误差 < 0.1%）。
    """
    lng0, lat0 = origin
    kx = M_PER_DEG * math.cos(math.radians(lat0))
    ky = M_PER_DEG

    def project(x: float, y: float) -> tuple[float, float]:
        return ((x - lng0) * kx, (y - lat0) * ky)

    return transform(project, geom).area / 1e6
