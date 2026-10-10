"""坐标转换单测：精确互逆性、境内偏移量级、境外直通、距离公式。"""

import pytest

from app.core import coords


def test_gcj02_bd09_roundtrip_exact() -> None:
    """GCJ02↔BD09 为解析互逆；sqrt/atan2 浮点链放大后误差约 4e-7 度（≈4cm），取 1e-6 上界。"""
    lng, lat = 116.404, 39.915
    back_lng, back_lat = coords.bd09_to_gcj02(*coords.gcj02_to_bd09(lng, lat))
    assert abs(back_lng - lng) < 1e-6
    assert abs(back_lat - lat) < 1e-6


def test_wgs84_gcj02_roundtrip_approx() -> None:
    """WGS84 逆变换为近似解，往返误差应 < 5 米（docs/02 §2.3 坐标系规范）。"""
    lng, lat = 116.397, 39.918
    g = coords.wgs84_to_gcj02(lng, lat)
    b = coords.gcj02_to_wgs84(*g)
    assert coords.haversine_m(b[0], b[1], lng, lat) < 5


def test_wgs84_offset_bounded() -> None:
    """境内 GCJ 偏移应在数百米量级（< 0.01 度）。"""
    lng, lat = 116.397, 39.918
    g = coords.wgs84_to_gcj02(lng, lat)
    assert abs(g[0] - lng) < 0.01
    assert abs(g[1] - lat) < 0.01


def test_out_of_china_no_gcj_offset() -> None:
    """境外坐标不做 GCJ 偏移；但 BD09 附加偏移仍应用（百度体系行为）。"""
    lng, lat = 2.3522, 48.8566  # 巴黎
    assert coords.wgs84_to_gcj02(lng, lat) == (lng, lat)
    bd = coords.wgs84_to_bd09(lng, lat)
    assert bd != (lng, lat)


def test_haversine_known_distance() -> None:
    """北纬 39.915 处经度差 0.01 度 ≈ 853 米。"""
    d = coords.haversine_m(116.404, 39.915, 116.414, 39.915)
    assert 830 < d < 875


def test_centroid_bd09_arithmetic_mean() -> None:
    """出入口质心 = 经纬度算术均值（小区尺度，误差可忽略）。"""
    pts = [(116.0, 39.0), (116.002, 39.0), (116.004, 39.0)]
    assert coords.centroid_bd09(pts) == (116.002, 39.0)


def test_centroid_bd09_single_point_identity() -> None:
    assert coords.centroid_bd09([(116.3, 39.9)]) == (116.3, 39.9)


def test_centroid_bd09_rejects_empty() -> None:
    with pytest.raises(ValueError):
        coords.centroid_bd09([])
