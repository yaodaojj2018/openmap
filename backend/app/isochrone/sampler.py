"""扇形采样几何（纯函数，无 IO）。

极坐标约定：theta 为方位角弧度，正东为 0、逆时针为正（与回放 Provider 的
_bearing_rad 一致）。米→度的局部平面近似在 1.3km 量级误差 < 0.1%，仅用于
探测点布设与结果渲染，不影响测时精度（测时由路径规划 API 决定）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.coords import M_PER_DEG
from app.mapapi.provider import BD09Point

# 与 core.coords.haversine_m 同一球体模型（经纬米/度同值），保证探针球面距离 ≈ 布设半径
M_PER_DEG_LAT = M_PER_DEG
M_PER_DEG_LNG_AT_EQUATOR = M_PER_DEG


@dataclass(frozen=True)
class Probe:
    """一个测时探针：方向 + 直线半径 + 探测点坐标（bd09）。"""

    theta: float
    radius_m: float
    lng: float
    lat: float


def offset_point(origin: BD09Point, theta: float, radius_m: float) -> tuple[float, float]:
    """从 origin 沿方位角 theta 偏移 radius_m 米后的经纬度。"""
    dx_m = radius_m * math.cos(theta)
    dy_m = radius_m * math.sin(theta)
    lng = origin[0] + dx_m / (M_PER_DEG_LNG_AT_EQUATOR * math.cos(math.radians(origin[1])))
    lat = origin[1] + dy_m / M_PER_DEG_LAT
    return lng, lat


def direction_angles(directions: int) -> list[float]:
    """等间隔方位角（含 0，不含 2π）。"""
    return [2 * math.pi * i / directions for i in range(directions)]


def probe_points(origin: BD09Point, directions: int, rings_m: Sequence[float]) -> list[Probe]:
    """阶段 A 探测点：方向 × 距离档 全网格。"""
    probes: list[Probe] = []
    for theta in direction_angles(directions):
        for radius in rings_m:
            lng, lat = offset_point(origin, theta, radius)
            probes.append(Probe(theta=theta, radius_m=radius, lng=lng, lat=lat))
    return probes


def midpoint_probe(origin: BD09Point, theta: float, r_lo: float, r_hi: float) -> Probe:
    """阶段 B 二分中点探针。"""
    radius = (r_lo + r_hi) / 2
    lng, lat = offset_point(origin, theta, radius)
    return Probe(theta=theta, radius_m=radius, lng=lng, lat=lat)
