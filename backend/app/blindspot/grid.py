"""盲区识别（docs/02 §3.3，全本地计算，零 API 成本）。

算法：中心点 extent×extent 米栅格化（cell 分辨率）→ 各关键设施类分别建 cKDTree，
向量化求每格中心到最近设施直线距离 → 超 threshold 判缺失 → 缺失栅格在米空间
unary_union 聚合 + buffer 平滑（8 邻接栅格在 50m 缓冲下融合）→ 环坐标反解回 BD09。

几何在米空间构造（buffer 各向同性），仅最终环坐标转经纬度——避免在度空间做
度量缓冲时经纬两轴米/度系数不同（cos 纬度）造成的各向异性畸变。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.spatial import cKDTree
from shapely import unary_union
from shapely.geometry import box

from app.core.config import Settings
from app.core.coords import local_delta_m, offset_lnglat
from app.mapapi.provider import BD09Point
from app.models.blindspot import BlindspotResult, BlindspotTypeResult
from app.models.common import Coord
from app.models.poi import PoiRecord


def compute_blindspot(
    origin: BD09Point,
    facilities: dict[str, list[PoiRecord]],
    settings: Settings,
    labels: dict[str, str],
    types: Sequence[str] | None = None,
) -> BlindspotResult:
    """栅格盲区识别入口。

    facilities 为清洗后的分类设施（键 = 类目）。types 为参与判定的类目清单，
    由编排层注入——只应包含本次实际检索成功的类目：空设施列表在本域语义是
    "真实无设施 → 全域缺失"，未选择/检索失败的类目没有数据，混入等于把
    数据缺失冒充成盲区事实。缺省回退 settings.blindspot_types。
    """
    cell = settings.blindspot_cell_m
    extent = settings.blindspot_extent_m
    side = extent // cell
    # 栅格中心（米空间，相对 origin）：-extent/2 起每格 cell，居中对齐
    centers = np.arange(side, dtype=float) * cell - extent / 2 + cell / 2
    gx, gy = np.meshgrid(centers, centers)  # 行 = 北向 y，列 = 东向 x
    grid_pts = np.column_stack([gx.ravel(), gy.ravel()])

    type_results: list[BlindspotTypeResult] = []
    missing_stack: list[np.ndarray] = []
    for key in settings.blindspot_types if types is None else types:
        recs = facilities.get(key, [])
        if recs:
            # 与 sampler/replay/时间场同源的米空间投影（local_delta_m），避免内联
            # 副本在常数修正时与时间场口径脱钩（BF-001 类漂移）
            fac_pts = np.array([local_delta_m(origin[0], origin[1], r.lng, r.lat) for r in recs])
            distances, _ = cKDTree(fac_pts).query(grid_pts)
            missing = distances > settings.blindspot_threshold_m
            worst = float(distances[missing].max()) if missing.any() else None
        else:
            # 该类完全无设施：全域缺失，worst 无意义（不存在"最远的最近设施"）
            missing = np.ones(grid_pts.shape[0], dtype=bool)
            worst = None
        missing_stack.append(missing)
        type_results.append(
            BlindspotTypeResult(
                type_key=key,
                label=labels.get(key, key),
                missing_cells=int(missing.sum()),
                worst_distance_m=worst,
                polygons=_missing_polygons(
                    missing, centers, cell, settings.blindspot_buffer_m, origin
                ),
            )
        )

    severity = np.stack(missing_stack).sum(axis=0) if missing_stack else np.zeros(0, int)
    return BlindspotResult(
        origin=Coord(lng=origin[0], lat=origin[1], crs="bd09"),
        extent_m=extent,
        cell_size_m=cell,
        grid_side=side,
        threshold_m=settings.blindspot_threshold_m,
        types=type_results,
        max_severity=int(severity.max()) if severity.size else 0,
        severe_cells=int((severity >= 2).sum()),
    )


def _missing_polygons(
    missing: np.ndarray,
    centers: np.ndarray,
    cell_m: float,
    buffer_m: float,
    origin: BD09Point,
) -> list[list[list[list[float]]]]:
    """缺失栅格 → 平滑盲区多边形（米空间聚合，BD09 输出）。

    unary_union 使共边缺失栅格自然成片；对角相邻（仅角点接触）的多边形由
    buffer(buffer_m ≥ 角点间隙 0) 融合——即 8 邻接聚类语义。
    centers 与距离计算共用同一份数组，保证多边形与栅格严格对齐。
    """
    side = centers.size
    ys, xs = np.nonzero(missing.reshape(side, side))
    if ys.size == 0:
        return []
    half = cell_m / 2
    cell_boxes = [
        box(centers[x] - half, centers[y] - half, centers[x] + half, centers[y] + half)
        for y, x in zip(ys, xs, strict=True)
    ]
    merged = unary_union(cell_boxes).buffer(buffer_m)
    polygons: list[list[list[list[float]]]] = []
    for geom in getattr(merged, "geoms", (merged,)):
        rings = [geom.exterior.coords, *(hole.coords for hole in geom.interiors)]
        polygons.append(
            [[[*offset_lnglat(origin[0], origin[1], x, y)] for x, y in ring] for ring in rings]
        )
    return polygons
