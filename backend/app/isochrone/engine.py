"""等时圈引擎：阶段 A 粗采样 → 阶段 B 并行二分 → 阶段 C 逐级拟合（编排层）。

Provider 由调用方注入（依赖倒置）；引擎只做几何决策与预算计数，
不感知 HTTP/重试/缓存细节（铁律 #2/#3）。预算口径（铁律 #7）：
64 探针粗采样 ≈ 2 批 + 每轮二分 1 批 × 3 轮 ≈ 5 次矩阵调用，远低于单次分析 40 次上限。
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from app.core.config import Settings
from app.isochrone.field import TimeField
from app.isochrone.fitter import fit_level
from app.isochrone.sampler import midpoint_probe, probe_points
from app.mapapi.provider import BD09Point, MapProvider
from app.models.common import Coord
from app.models.isochrone import IsochroneLevel, IsochroneResult


class IsochroneError(ValueError):
    """输入非法（levels 越界等），路由层据此映射 422。"""


@dataclass(frozen=True)
class IsochroneComputation:
    """等时圈计算产物：响应模型 + 射线时间场。

    时间场随结果一并返回，供覆盖判定二级漏斗复用（docs/02 §3.2"圈内点用
    时间场直接插值"），避免为估算设施耗时重新采样。
    """

    result: IsochroneResult
    field: TimeField


ProgressHook = Callable[[str, float], Awaitable[None]]
"""进度钩子：(阶段名 "sampling"|"fitting", 阶段内局部进度 0-1)。

引擎只报本地视角（域层不感知任务语义，铁律 #2）；全局进度窗口映射
（docs/02 §5.1 的 30%→70%）由任务编排层（tasks/pipeline）负责。
"""


async def _report(hook: ProgressHook | None, stage: str, frac: float) -> None:
    if hook is not None:
        await hook(stage, frac)


async def compute_isochrone(
    provider: MapProvider,
    settings: Settings,
    origin: BD09Point,
    levels_min: list[int],
    on_progress: ProgressHook | None = None,
) -> IsochroneComputation:
    """计算多级步行等时圈（v1：射线插值 + 闭合样条，docs/02 §3.1）。

    on_progress 可选：阶段 A/B 进度以 "sampling" 上报、阶段 C 以 "fitting" 上报。
    """
    levels = sorted(set(levels_min))
    if not levels or any(not 1 <= m <= 30 for m in levels):
        raise IsochroneError("levels_min 需为 1-30 分钟内的非空集合")

    log = structlog.get_logger(__name__)
    field = TimeField(settings.isochrone_directions)
    batches = 0

    # 阶段 A：方向 × 距离档全网格粗采样（1 次矩阵调用 ≈ 采样预算的 1/4，B 至多 3 次）
    await _report(on_progress, "sampling", 0.0)
    probes = probe_points(origin, settings.isochrone_directions, settings.isochrone_rings_m)
    legs = await provider.route_matrix(origin, [(p.lng, p.lat) for p in probes])
    field.ingest(probes, legs)
    batches += math.ceil(len(probes) / settings.matrix_batch_size)
    await _report(on_progress, "sampling", 0.25)

    # 阶段 B：仅对最大级别二分细化——外圈是产品核心输出，内圈粗粒度可接受（v1 取舍）
    target_s = levels[-1] * 60
    for round_no in range(settings.isochrone_refine_rounds):
        brackets = field.refine_brackets(target_s)
        if not brackets:
            break
        mids = [midpoint_probe(origin, theta, lo, hi) for theta, lo, hi in brackets]
        legs = await provider.route_matrix(origin, [(p.lng, p.lat) for p in mids])
        field.ingest(mids, legs)
        batches += math.ceil(len(mids) / settings.matrix_batch_size)
        await _report(
            on_progress, "sampling", 0.25 + 0.75 * (round_no + 1) / settings.isochrone_refine_rounds
        )

    # 阶段 C：逐级拟合（时间场复用，无额外 API 消耗）
    await _report(on_progress, "fitting", 0.0)
    out_levels: list[IsochroneLevel] = []
    for idx, minute in enumerate(levels):
        rings, area_km2, confidence = fit_level(origin, field.level_radius(minute * 60))
        out_levels.append(
            IsochroneLevel(
                level_min=minute,
                coordinates=rings,
                area_km2=area_km2,
                confidence=confidence,
            )
        )
        await _report(on_progress, "fitting", (idx + 1) / len(levels))

    log.info(
        "isochrone.done",
        origin=[round(origin[0], 6), round(origin[1], 6)],
        levels=levels,
        probes=field.probe_count,
        matrix_batches=batches,
    )
    return IsochroneComputation(
        result=IsochroneResult(
            origin=Coord(lng=origin[0], lat=origin[1], crs="bd09"),
            levels=out_levels,
            probe_count=field.probe_count,
            matrix_batches=batches,
        ),
        field=field,
    )
