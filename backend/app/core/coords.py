"""坐标系转换（纯函数，无 IO）。

内部规范坐标系为 BD09（百度体系原生）。转换公式为公开的经典实现：
- WGS84 ↔ GCJ02：国测局加密偏移的级数近似（非精确逆变换，误差米级以下）
- GCJ02 ↔ BD09：解析互逆（精确）

中国境外坐标不做偏移（GCJ 加密仅覆盖境内）。
"""

from __future__ import annotations

import math

_X_PI = math.pi * 3000.0 / 180.0
_KRASOVSKY_A = 6378245.0  # 克拉索夫斯基椭球长半轴
_EE = 0.00669342162296594323  # 第一偏心率平方

LngLat = tuple[float, float]

# 球体地球模型（haversine 同源）：米/度在球面模型下经纬同值。
# isochrone/sampler 的平面投影必须用同一组常数，否则探针直线距离会漂移
# （曾因椭球/球体常数混用导致 ~0.6% 距离膨胀，触发回放模式大面积误判不可达）。
EARTH_RADIUS_M = 6371008.8
M_PER_DEG = math.pi * EARTH_RADIUS_M / 180.0  # ≈ 111194.93


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * math.pi * x)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def out_of_china(lng: float, lat: float) -> bool:
    """粗略判断是否中国境外（境外不适用 GCJ 偏移）。"""
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def wgs84_to_gcj02(lng: float, lat: float) -> LngLat:
    if out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_KRASOVSKY_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_KRASOVSKY_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> LngLat:
    """近似逆变换：以偏移后坐标反推偏移量，误差米级以下。"""
    if out_of_china(lng, lat):
        return lng, lat
    glng, glat = wgs84_to_gcj02(lng, lat)
    return lng * 2 - glng, lat * 2 - glat


def gcj02_to_bd09(lng: float, lat: float) -> LngLat:
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * _X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * _X_PI)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lng: float, lat: float) -> LngLat:
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lng: float, lat: float) -> LngLat:
    g_lng, g_lat = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09(g_lng, g_lat)


def bd09_to_wgs84(lng: float, lat: float) -> LngLat:
    g_lng, g_lat = bd09_to_gcj02(lng, lat)
    return gcj02_to_wgs84(g_lng, g_lat)


def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """两点球面距离（米），用于本地粗筛与去重，零 API 成本。"""
    rad = math.pi / 180.0
    dlat = (lat2 - lat1) * rad
    dlng = (lng2 - lng1) * rad
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def local_delta_m(lng1: float, lat1: float, lng2: float, lat2: float) -> tuple[float, float]:
    """局部平面投影：点 2 相对点 1 的位移（东向/北向，米）。

    与 sampler/replay 的米空间方位角约定同源（M_PER_DEG × cos 纬度），
    供盲区栅格布设与时间场点位反解使用；1.5km 量级误差 < 0.1%。
    """
    return (
        (lng2 - lng1) * M_PER_DEG * math.cos(math.radians(lat1)),
        (lat2 - lat1) * M_PER_DEG,
    )


def offset_lnglat(lng: float, lat: float, dx_m: float, dy_m: float) -> LngLat:
    """local_delta_m 的逆变换：给定点沿平面位移（东/北，米）后的经纬度。"""
    return (
        lng + dx_m / (M_PER_DEG * math.cos(math.radians(lat))),
        lat + dy_m / M_PER_DEG,
    )


def centroid_bd09(points: list[tuple[float, float]]) -> tuple[float, float]:
    """多源点（小区出入口）的代表中心：经纬度算术均值（bd09）。

    用途：POI 检索中心 / 盲区原点 / 覆盖矩阵验证源点 / 报告 origin。点集同属
    一个社区（间距数百米级），球面曲率与跨经度纬向收缩的误差在米级以下，
    无需大地测量质心。
    """
    if not points:
        raise ValueError("centroid_bd09 需要至少一个点")
    n = len(points)
    return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n
