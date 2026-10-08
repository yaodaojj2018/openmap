"""等时圈引擎：A 粗采样 → B 并行二分 → C 双法校验 → D 加密 → E 逐级拟合（编排层）。

Provider 由调用方注入（依赖倒置）；引擎只做几何决策与预算计数，
不感知 HTTP/重试/缓存细节（铁律 #2/#3）。预算口径（铁律 #7）：
64 探针粗采样 ≈ 2 批 + 每轮二分 1 批 × 3 轮 + 加密 ≤ 1 批 ≈ 6 次矩阵调用，
远低于单次分析 40 次上限。

阶段 C/D（docs/02 §3.1，M4）：把散点时间场插值成网格并提等值线作为第二法，
与射线样条（第一法）逐方向比对；偏差超阈值与相邻半径突变的方位补采探针，
合并为一次批量矩阵调用。第二法只做校验与定位，几何出口始终是样条——
等值线面不直接出图（前端与响应契约仍是单 Polygon）。
矩阵失败的降级语义（docs/02 §3.4）：等时圈不走逐条中间级（64 探针会爆
预算），直接落第三级直线×1.3 模型估算，置信度封顶并在结果上声明 degraded_reason。
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from app.core.budget import current_budget
from app.core.config import Settings
from app.core.coords import haversine_m
from app.isochrone.field import TimeField
from app.isochrone.fitter import fit_level
from app.isochrone.grid import GridField, build_grid, contour_polygons
from app.isochrone.reconcile import densify_probes, enclave_probes, grid_consistency
from app.isochrone.sampler import Probe, midpoint_probe, probe_points
from app.mapapi.provider import BD09Point, ErrorKind, MapApiError, MapProvider
from app.models.common import Coord
from app.models.isochrone import IsochroneLevel, IsochroneResult, RouteLeg


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


def _estimate_leg(
    origin: BD09Point, target: BD09Point, speed_mps: float, detour: float
) -> RouteLeg:
    """降级链第三级：直线距离 × 经验系数 ÷ 步速（docs/02 §3.4）。

    模型口径全程可达（无路网阻挡信息），故只作兜底并配低置信度封顶。
    """
    distance = haversine_m(origin[0], origin[1], target[0], target[1]) * detour
    return RouteLeg(distance_m=distance, duration_s=distance / speed_mps)


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
    degraded_reason: str | None = None

    async def measure(points: list[Probe]) -> tuple[list[RouteLeg], str | None]:
        """批量测时 → (测时结果, 降级原因 | None)。

        矩阵故障时回落直线×1.3 模型估算（docs/02 §3.4 第三级），返回原因由调用方
        决定是否升级——阶段 A/B 用原因标注整份等时圈，可选的加密层只在本地丢弃。
        返回长度恒等于 points：Provider 丢行（违反 1:1 返回契约）等价上游故障，
        不拦截则下游 zip(strict) 抛 ValueError 击穿降级守卫（BF-006 同类）。
        """
        nonlocal batches
        dests = [(p.lng, p.lat) for p in points]
        try:
            legs = await provider.route_matrix(origin, dests)
            if len(legs) != len(dests):
                raise ValueError(f"route_matrix 返回 {len(legs)} 行 ≠ 请求 {len(dests)} 行")
        except (MapApiError, ValueError) as exc:
            exhausted = isinstance(exc, MapApiError) and exc.kind is ErrorKind.BUDGET_EXCEEDED
            reason = "budget:exhausted" if exhausted else "matrix:unavailable"
            log.warning("isochrone.matrix_degraded", reason=reason, error=str(exc))
            return [
                _estimate_leg(
                    origin, dest, settings.fallback_walk_speed_mps, settings.fallback_detour_factor
                )
                for dest in dests
            ], reason
        batches += math.ceil(len(dests) / settings.matrix_batch_size)
        return legs, None

    # 阶段 A：方向 × 距离档全网格粗采样（1 次矩阵调用 ≈ 采样预算的 1/4，B 至多 3 次）
    await _report(on_progress, "sampling", 0.0)
    probes = probe_points(origin, settings.isochrone_directions, settings.isochrone_rings_m)
    legs, reason = await measure(probes)
    degraded_reason = degraded_reason or reason  # 首因保留（A 失败后 B 不改写）
    field.ingest(probes, legs)
    await _report(on_progress, "sampling", 0.25)

    # 阶段 B：仅对最大级别二分细化——外圈是产品核心输出，内圈粗粒度可接受（v1 取舍）
    target_s = levels[-1] * 60
    for round_no in range(settings.isochrone_refine_rounds):
        brackets = field.refine_brackets(target_s)
        if not brackets:
            break
        mids = [midpoint_probe(origin, theta, lo, hi) for theta, lo, hi in brackets]
        legs, reason = await measure(mids)
        degraded_reason = degraded_reason or reason
        field.ingest(mids, legs)
        await _report(
            on_progress, "sampling", 0.25 + 0.75 * (round_no + 1) / settings.isochrone_refine_rounds
        )

    # 阶段 C：网格时间场 + 等值线（纯几何零配额），阶段 D：偏差/飞地加密（≤1 批）
    await _report(on_progress, "fitting", 0.0)
    extent_m = float(settings.isochrone_grid_extent_m)
    densified = 0

    def _build() -> GridField | None:
        """散点时间场 → 规则网格（第二法）。

        外推虚拟环半径取网格覆盖半径：快方向的级别圈本就落在最远探针之外，
        没有这一环等值线无法闭合，第二法对这些方向直接失效。
        """
        points, times = field.scatter(extrapolate_to_m=extent_m)
        return build_grid(points, times, float(settings.isochrone_grid_step_m), extent_m)

    grid = _build()
    if grid is not None:
        coarse = field.level_radius(levels[-1] * 60)
        extra = densify_probes(
            coarse,
            contour_polygons(grid, levels[-1] * 60),
            origin,
            settings.isochrone_verify_tolerance_m,
            extent_m,
            settings.isochrone_densify_max_probes,
        )
        extra += enclave_probes(
            coarse,
            origin,
            settings.isochrone_enclave_jump_ratio,
            extent_m,
            settings.isochrone_densify_max_probes - len(extra),
        )
        if extra:
            needed = math.ceil(len(extra) / settings.matrix_batch_size)
            budget = current_budget()
            if budget is not None and budget.remaining < needed:
                # 加密是精度优化而非正确性层：预算不足就整层跳过，不做半截采样
                log.warning(
                    "isochrone.densify_skipped",
                    probes=len(extra),
                    needed_batches=needed,
                    remaining=budget.remaining,
                )
            else:
                extra_legs, extra_reason = await measure(extra)
                if extra_reason is None:
                    field.ingest(extra, extra_legs)
                    densified = len(extra)
                    grid = _build()  # 新测量进入时间场，第二法随之更新
                else:
                    # 加密是可选精度层：失败不升级降级标志，主几何仍是实测口径
                    log.warning("isochrone.densify_unavailable", probes=len(extra))

    # 阶段 E：逐级拟合（时间场复用，零额外 API），置信度按双法一致性折算
    out_levels: list[IsochroneLevel] = []
    for idx, minute in enumerate(levels):
        level_s = minute * 60
        radii = field.level_radius(level_s)
        consistency = (
            grid_consistency(
                radii,
                contour_polygons(grid, level_s),
                settings.isochrone_verify_tolerance_m,
                extent_m,
            )
            if grid is not None
            else None
        )
        rings, area_km2, confidence = fit_level(origin, radii, consistency=consistency)
        if degraded_reason is not None:
            # 估算口径：样条拟合照常，但置信度封顶——报告与前端据此区分实测/估算
            confidence = min(confidence, settings.fallback_confidence_cap)
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
        densified=densified,
        matrix_batches=batches,
    )
    return IsochroneComputation(
        result=IsochroneResult(
            origin=Coord(lng=origin[0], lat=origin[1], crs="bd09"),
            levels=out_levels,
            probe_count=field.probe_count,
            matrix_batches=batches,
            method="straight-estimate" if degraded_reason else "ray-spline-v1",
            degraded_reason=degraded_reason,
        ),
        field=field,
    )
