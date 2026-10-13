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
    residential: list[PoiRecord] | None = None,
    industrial: list[PoiRecord] | None = None,
) -> BlindspotResult:
    """栅格盲区识别入口。

    facilities 为清洗后的分类设施（键 = 类目）。types 为参与判定的类目清单，
    由编排层注入——只应包含本次实际检索成功的类目：空设施列表在本域语义是
    "真实无设施 → 全域缺失"，未选择/检索失败的类目没有数据，混入等于把
    数据缺失冒充成盲区事实。缺省回退 settings.blindspot_types。

    residential 为居住用地 POI（正向掩膜，docs/06 §6）：非 None 且非空时只对
    「格心到最近居住 POI ≤ blindspot_residential_radius_m」的居住格判缺失，公园/
    水域/道路等非居住格不计入缺口（消除"湖泊被标成缺教育"的假警报）。None/空 =
    不掩膜（全格参与），编排层在检索失败/空命中时传 None 降级为全域口径并显式标记。

    industrial 为工业园 POI（负向修正，docs/06 §6）：居住格若同时落在
    「距最近工业园 POI ≤ blindspot_industrial_radius_m」内则扣除——正向掩膜的 200m
    缓冲会漫入紧邻的工业园（城北万象城实测命中 20+），故加一层负向把园区核心扣掉。
    None/空 = 不扣（编排层在工业园检索失败/空命中时传 None，仅记 warning 不降级）。
    """
    cell = settings.blindspot_cell_m
    extent = settings.blindspot_extent_m
    side = extent // cell
    # 栅格中心（米空间，相对 origin）：-extent/2 起每格 cell，居中对齐
    centers = np.arange(side, dtype=float) * cell - extent / 2 + cell / 2
    gx, gy = np.meshgrid(centers, centers)  # 行 = 北向 y，列 = 东向 x
    grid_pts = np.column_stack([gx.ravel(), gy.ravel()])

    # 居住掩膜（docs/06 §6.4）：居住 POI 建 cKDTree，格心距最近居住 POI ≤ 半径判居住格。
    # 非居住格在每类判定中恒为 False → 不参与缺失/严重度，天然从结果中排除。
    if residential:
        res_pts = np.array([local_delta_m(origin[0], origin[1], r.lng, r.lat) for r in residential])
        res_dist, _ = cKDTree(res_pts).query(grid_pts)
        res_mask = res_dist <= settings.blindspot_residential_radius_m
    else:
        res_mask = np.ones(grid_pts.shape[0], dtype=bool)
    # 工业园负向修正（docs/06 §6）：居住格若同时落在工业园 POI 半径内则扣除。
    # 负向只作用于「已判居住」的格，天然有界——不会反向吃掉远离小区的真居住区。
    if industrial:
        ind_pts = np.array([local_delta_m(origin[0], origin[1], r.lng, r.lat) for r in industrial])
        ind_dist, _ = cKDTree(ind_pts).query(grid_pts)
        ind_mask = ind_dist <= settings.blindspot_industrial_radius_m
    else:
        ind_mask = np.zeros(grid_pts.shape[0], dtype=bool)
    effective_res_mask = res_mask & ~ind_mask
    industrial_cells = int((res_mask & ind_mask).sum())
    total_cells = int(grid_pts.shape[0])

    type_results: list[BlindspotTypeResult] = []
    missing_stack: list[np.ndarray] = []
    for key in settings.blindspot_types if types is None else types:
        recs = facilities.get(key, [])
        if recs:
            # 与 sampler/replay/时间场同源的米空间投影（local_delta_m），避免内联
            # 副本在常数修正时与时间场口径脱钩（BF-001 类漂移）
            fac_pts = np.array([local_delta_m(origin[0], origin[1], r.lng, r.lat) for r in recs])
            distances, _ = cKDTree(fac_pts).query(grid_pts)
            missing = (distances > settings.blindspot_threshold_m) & effective_res_mask
            worst = float(distances[missing].max()) if missing.any() else None
        else:
            # 该类完全无设施：有效居住格全域缺失，worst 无意义（不存在"最远的最近设施"）
            missing = effective_res_mask.copy()
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
        mask_enabled=bool(residential),
        residential_cells=int(effective_res_mask.sum()),
        excluded_cells=total_cells - int(effective_res_mask.sum()),
        industrial_cells=industrial_cells,
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
