"""网格时间场与等值线提取（纯函数域层，无 IO）。

双法交叉验证的"第二法"（docs/02 §3.1 阶段 C）：把散点测时插值成规则网格，
再提取时间等值线——与射线样条拟合（第一法）互为独立口径，径向偏差超阈值的
区段触发局部加密重采样。样条仍是主法（保证闭合、平滑），第二法负责校验与
加密定位；两者不一致且加密后仍未收敛时，置信度按一致性打折。

实现取舍：
- `scipy.interpolate.griddata` 线性插值 + nearest 填补凸包外 NaN（两遍标准做法）；
- 自研 marching squares（约百行纯函数，零新增依赖，不引 scikit-image）：
  逐格在四边上线性插值求等值线段，对角模糊用格心值仲裁；
- `shapely.ops.polygonize` 把线段闭合成面——线段端点由同一对格点算出，
  坐标逐位相同，天然满足 polygonize 的 noded 前置条件。

坐标一律为米制局部平面（与 sampler/fitter 同一套投影），出口再转 BD09。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import griddata
from scipy.spatial import QhullError
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

# marching squares 边编号（格角 bl=(i,j) / br=(i+1,j) / tr=(i+1,j+1) / tl=(i,j+1)）：
# 0=下边 1=右边 2=上边 3=左边。case 位：bl=1, br=2, tr=4, tl=8。
# 每对边必"一内一外"（由 _edge_pair_test 逐 case 校验），否则线性插值系数会跑出
# [0,1] 把等值点甩到网格之外（曾把 case 12 误写成 (0,2)——那是 case 9 的镜像）。
# 只列非退化 case：全内（15）与全外（0）在 extract_segments 里先行剔除，不进表。
_EDGE_PAIRS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((3, 0),),  # bl
    2: ((0, 1),),  # br
    3: ((1, 3),),  # bl+br
    4: ((1, 2),),  # tr
    6: ((0, 2),),  # br+tr
    7: ((2, 3),),  # bl+br+tr
    8: ((2, 3),),  # tl
    9: ((0, 2),),  # bl+tl
    11: ((1, 2),),  # bl+br+tl
    12: ((1, 3),),  # tr+tl
    13: ((0, 1),),  # bl+tr+tl
    14: ((3, 0),),  # br+tr+tl
}
# 对角模糊（case 5：bl+tr 在内；case 10：br+tl 在内）由格心值仲裁：
# 中心在内 → 两个"内"角连通，边界分离两个"外"角。
_AMBIGUOUS: dict[int, tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]] = {
    5: (((0, 1), (2, 3)), ((3, 0), (1, 2))),
    10: (((3, 0), (1, 2)), ((0, 1), (2, 3))),
}


@dataclass(frozen=True)
class GridField:
    """规则网格时间场：values[j, i] = 格点 (x0+i·step, y0+j·step) 的步行秒数。"""

    x0: float
    y0: float
    step: float
    values: np.ndarray

    def point(self, i: int, j: int) -> tuple[float, float]:
        return self.x0 + i * self.step, self.y0 + j * self.step


def build_grid(
    points: Sequence[tuple[float, float]],
    values: Sequence[float],
    step_m: float,
    extent_m: float,
) -> GridField | None:
    """散点 → 规则网格时间场（线性 + nearest 两遍插值）。

    样本 < 3 或共线退化（QhullError）时返回 None——调用方跳过第二法校验，
    不虚构几何。
    """
    if step_m <= 0 or extent_m <= 0 or len(points) < 3:
        return None
    pts = np.asarray(points, dtype=float)
    vals = np.asarray(values, dtype=float)
    axis = np.arange(-extent_m, extent_m + step_m * 0.5, step_m)
    gx, gy = np.meshgrid(axis, axis)
    try:
        linear = griddata(pts, vals, (gx, gy), method="linear")
    except (QhullError, ValueError):
        return None  # 样本退化（共线/重合）：第二法不可用
    nearest = griddata(pts, vals, (gx, gy), method="nearest")
    grid = np.where(np.isnan(linear), nearest, linear)
    return GridField(x0=float(axis[0]), y0=float(axis[0]), step=float(step_m), values=grid)


def extract_segments(
    grid: GridField, level_s: float
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """marching squares：提取网格上 level_s 等值线的米制线段。"""
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    values = grid.values
    ny, nx = values.shape
    for j in range(ny - 1):
        for i in range(nx - 1):
            corners = (
                float(values[j, i]),
                float(values[j, i + 1]),
                float(values[j + 1, i + 1]),
                float(values[j + 1, i]),
            )
            if not all(math.isfinite(v) for v in corners):
                continue  # 含无数据格：跳过，不跨越插值
            code = sum(1 << bit for bit, value in enumerate(corners) if value > level_s)
            if code in (0, 15):
                continue
            pairs = _EDGE_PAIRS.get(code)
            if pairs is None:
                center = sum(corners) / 4.0
                inside, outside = _AMBIGUOUS[code]
                pairs = inside if center > level_s else outside
            for edge_a, edge_b in pairs:
                segs.append(
                    (
                        _edge_point(grid, i, j, edge_a, corners, level_s),
                        _edge_point(grid, i, j, edge_b, corners, level_s),
                    )
                )
    return segs


def contour_polygons(grid: GridField, level_s: float, min_area_m2: float = 0.0) -> list[Polygon]:
    """等值线 → 面（按面积降序）。无可用线段（未闭合）时返回空列表。

    多边形无法闭合的常见原因：该级别的边界落在网格范围之外（快方向外推
    封顶仍到不了级别耗时）——此时返回空，由调用方退回样条单法，不虚构几何。
    """
    segs = extract_segments(grid, level_s)
    if not segs:
        return []
    noded = unary_union(MultiLineString([LineString(seg) for seg in segs]))
    polys = [poly for poly in polygonize([noded]) if poly.area > min_area_m2]
    return sorted(polys, key=lambda poly: poly.area, reverse=True)


def radial_distance(geom: BaseGeometry, theta: float, max_r_m: float) -> float | None:
    """原点沿 theta 方向与几何边界的最大交点距离（米）；无交点返回 None。

    用于双法径向比对：样条多边形与等值线面在同一方位角上的边界半径之差，
    即该区段的拟合偏差。
    """
    ray = LineString([(0.0, 0.0), (max_r_m * math.cos(theta), max_r_m * math.sin(theta))])
    hits = geom.intersection(ray)
    distances = [math.hypot(x, y) for x, y in _coords(hits)]
    return max(distances) if distances else None


def _coords(geom: BaseGeometry) -> list[tuple[float, float]]:
    """递归取几何（含 GeometryCollection/Multi*）的全部顶点坐标。"""
    if geom.is_empty:
        return []
    if isinstance(geom, Point):
        return [(geom.x, geom.y)]
    if isinstance(geom, LineString):
        return [(x, y) for x, y in geom.coords]
    if isinstance(geom, Polygon):
        return [(x, y) for x, y in geom.exterior.coords]
    out: list[tuple[float, float]] = []
    for part in getattr(geom, "geoms", ()):
        out.extend(_coords(part))
    return out


def _edge_point(
    grid: GridField,
    i: int,
    j: int,
    edge: int,
    corners: tuple[float, float, float, float],
    level_s: float,
) -> tuple[float, float]:
    """格内某条边上等值点的线性插值坐标（米制）。"""
    bl, br, tr, tl = corners
    span = {
        0: ((i, j), (i + 1, j), bl, br),  # 下边
        1: ((i + 1, j), (i + 1, j + 1), br, tr),  # 右边
        2: ((i, j + 1), (i + 1, j + 1), tl, tr),  # 上边
        3: ((i, j), (i, j + 1), bl, tl),  # 左边
    }[edge]
    (i_a, j_a), (i_b, j_b), v_a, v_b = span
    x_a, y_a = grid.point(i_a, j_a)
    x_b, y_b = grid.point(i_b, j_b)
    ratio = 0.5 if v_b == v_a else (level_s - v_a) / (v_b - v_a)
    return x_a + ratio * (x_b - x_a), y_a + ratio * (y_b - y_a)
