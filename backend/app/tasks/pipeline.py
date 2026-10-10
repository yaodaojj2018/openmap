"""分析流水线编排（docs/02 §5.1 时序 / §5.2 状态机）。

职责边界：把 Provider 注入各域层入口并推进阶段与进度；降级决策收口在此层
（域层保持纯函数/纯编排，铁律 #2）。api_call_stats 由 CountingProvider 在协议层计数。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol

import structlog
from shapely import unary_union
from shapely.geometry.base import BaseGeometry

from app.blindspot.grid import compute_blindspot
from app.core.budget import budget_scope, current_budget
from app.core.config import Settings
from app.core.geometry import geometry_to_rings, local_area_km2, rings_to_geometry
from app.coverage.funnel import (
    classify_facilities,
    merge_matrix_verdicts,
    merge_walking_verdicts,
    summarize_categories,
)
from app.isochrone.engine import IsochroneComputation, compute_isochrone
from app.mapapi.provider import BD09Point, MapApiError, MapProvider
from app.models.common import Coord
from app.models.coverage import CoverageResult
from app.models.geocode import GeocodeCandidate
from app.models.isochrone import IsochroneLevel, IsochroneResult, RouteLeg
from app.models.poi import PoiRecord, PoiSearchResult
from app.models.report import AnalysisReport
from app.models.task import AnalysisParams, TaskStage
from app.poi.service import load_taxonomy, search_categories

# 各阶段的全局进度窗口（docs/02 §5.1：等时圈 30%→70%、POI 70%→85%、覆盖 85%→92%、盲区 92%→97%）。
STAGE_WINDOWS: dict[TaskStage, tuple[float, float]] = {
    TaskStage.resolving: (0.02, 0.30),
    TaskStage.sampling: (0.30, 0.58),
    TaskStage.fitting: (0.58, 0.70),
    TaskStage.poi: (0.70, 0.85),
    TaskStage.coverage: (0.85, 0.92),
    TaskStage.blindspot: (0.92, 0.97),
}


class TaskReporter(Protocol):
    """编排层 → 任务管理器的进度上报接口（管理器负责状态机落档与事件扇出）。"""

    async def on_stage(self, stage: TaskStage) -> None: ...

    async def on_progress(self, progress: float) -> None: ...


class CountingProvider:
    """按端点计数的 Provider 装饰器（api_call_stats 数据来源，docs/02 §7.5）。

    计数口径是协议方法调用（逻辑调用）；BaiduClient 内部的批量分片/重试属于
    传输层细节，不在此感知——批量成本口径由 IsochroneResult.matrix_batches 补充。
    """

    def __init__(self, inner: MapProvider) -> None:
        self.inner = inner
        self.name = inner.name  # 协议要求可写变量（与实现类对齐），非只读属性
        self.counts: dict[str, int] = {}

    def _tick(self, endpoint: str) -> None:
        self.counts[endpoint] = self.counts.get(endpoint, 0) + 1

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        self._tick("geocode")
        return await self.inner.geocode(address, city)

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        self._tick("search_pois")
        return await self.inner.search_pois(query, center, radius_m, page_size, max_pages)

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        self._tick("route_matrix")
        return await self.inner.route_matrix(origin, destinations)

    async def walking_route(self, origin: BD09Point, destination: BD09Point) -> RouteLeg:
        self._tick("walking_route")
        return await self.inner.walking_route(origin, destination)

    async def close(self) -> None:
        await self.inner.close()


async def _verify_by_walking(
    provider: MapProvider, origin: BD09Point, destinations: list[BD09Point]
) -> list[RouteLeg | None] | None:
    """降级链第二级：逐条步行规划实测（docs/02 §3.4，预算门控）。

    剩余名额不足以覆盖全部目的地时整层跳过（返回 None，编排层落到第三级
    保留插值口径）——逐条部分执行会让"哪些设施被实测"取决于失败时机，
    引入选择偏差。单条失败位置为 None：该设施回落插值口径，不冒充实测。
    """
    budget = current_budget()
    if budget is not None and budget.remaining < len(destinations):
        return None
    legs: list[RouteLeg | None] = []
    for dest in destinations:
        try:
            legs.append(await provider.walking_route(origin, dest))
        except MapApiError:
            legs.append(None)  # 含预算中途耗尽（BUDGET_EXCEEDED）：单条回落第三级
    return legs


def resolve_levels(minutes: int, settings: Settings) -> list[int]:
    """阈值分钟 → 多级圈级别：配置级别中 < 阈值者 + 阈值本身（如 15 → [5, 10, 15]）。"""
    return sorted({m for m in settings.isochrone_levels_min if m < minutes} | {minutes})


def _rings_union(rings_list: list[list[list[list[float]]]]) -> BaseGeometry | None:
    """GeoJSON MultiPolygon 环组 → shapely 并集几何（任一源多边形即并集，空集返回 None）。"""
    geoms = [g for poly in rings_list if (g := rings_to_geometry(poly)) is not None]
    return unary_union(geoms) if geoms else None


def _merge_iso(
    computations: list[IsochroneComputation],
    levels_min: list[int],
    origin: BD09Point,
    origins: list[Coord],
) -> IsochroneResult:
    """多源等时圈并集（docs/02 §3.1）：逐级把各源多边形 unary_union 成 MultiPolygon。

    单源时 = 直接透传该源结果（1 元素 MultiPolygon）；多源并集后可达集可能不连通。
    probe_count/matrix_batches 求和、confidence 取各源最小值（并集置信度不高于最弱源）。
    """
    levels: list[IsochroneLevel] = []
    for idx, minute in enumerate(levels_min):
        union = _rings_union(
            [poly for comp in computations for poly in comp.result.levels[idx].coordinates]
        )
        rings = geometry_to_rings(union) if union is not None and not union.is_empty else []
        area_km2 = (
            round(local_area_km2(union, origin), 4)
            if union is not None and not union.is_empty
            else 0.0
        )
        confidence = min(comp.result.levels[idx].confidence for comp in computations)
        levels.append(
            IsochroneLevel(
                level_min=minute, coordinates=rings, area_km2=area_km2, confidence=confidence
            )
        )
    degraded_reason = next(
        (c.result.degraded_reason for c in computations if c.result.degraded_reason is not None),
        None,
    )
    return IsochroneResult(
        origin=Coord(lng=origin[0], lat=origin[1], crs="bd09"),
        origins=origins,
        levels=levels,
        probe_count=sum(c.result.probe_count for c in computations),
        matrix_batches=sum(c.result.matrix_batches for c in computations),
        method="straight-estimate" if degraded_reason else "ray-spline-v1",
        degraded_reason=degraded_reason,
    )


async def run_analysis(
    task_id: str,
    params: AnalysisParams,
    provider: MapProvider,
    settings: Settings,
    reporter: TaskReporter,
) -> AnalysisReport:
    """执行完整分析流水线，产出报告（含降级标记与 API 统计）。

    全程运行在 QuotaBudget 作用域内（docs/02 §3.4）：出站 HTTP 以
    settings.analysis_api_budget 为硬上限，超限由适配器抛 BUDGET_EXCEEDED，
    各阶段按降级链收口（失败语义见 _run_analysis docstring）。gather 派生的
    子任务共享同一预算实例，物理口径统计（http_calls/cache_hits/retries）并入报告。
    """
    with budget_scope(settings.analysis_api_budget):
        return await _run_analysis(task_id, params, provider, settings, reporter)


async def _run_analysis(
    task_id: str,
    params: AnalysisParams,
    provider: MapProvider,
    settings: Settings,
    reporter: TaskReporter,
) -> AnalysisReport:
    """流水线主体（在 run_analysis 的预算作用域内执行）。

    失败语义：等时圈失败 = 整体失败（无圈则无报告）；单类目 POI 失败 = 降级标记
    继续（docs/02 §3.4：POI 为空显式报告"该类 0 个"而非报错）。
    """
    counting = CountingProvider(provider)
    degraded: list[str] = []
    log = structlog.get_logger(__name__)

    # ① resolving：坐标规范化。地址解析在创建前的 /geocode 完成（docs/02 §5.1 时序），
    #    任务内只做 crs → bd09 纯本地转换，不消耗 API。
    await reporter.on_stage(TaskStage.resolving)
    origin_bd09 = params.origin.to_bd09()  # 代表中心（多源 = 出入口质心）
    origins_bd09 = [c.to_bd09() for c in params.entry_points]
    entry_point_coords = [Coord(lng=o[0], lat=o[1], crs="bd09") for o in origins_bd09]
    levels_min = resolve_levels(params.minutes, settings)
    await reporter.on_progress(STAGE_WINDOWS[TaskStage.resolving][1])

    # ② sampling + fitting：多源逐出入口跑等时圈，各源进度映射进 sampling/fitting 窗口
    #    的等分子区间，保证全局进度单调递增（docs/02 §3.1 多源并集）。
    await reporter.on_stage(TaskStage.sampling)
    seen_stages: set[str] = {"sampling"}  # sampling 已上报，避免 engine 首次回调重复推进
    n_sources = len(origins_bd09)
    computations: list[IsochroneComputation] = []
    for i, src in enumerate(origins_bd09):
        src_lo = i / n_sources
        src_hi = (i + 1) / n_sources

        async def engine_hook(
            stage_name: str, frac: float, _lo: float = src_lo, _hi: float = src_hi
        ) -> None:
            if stage_name not in seen_stages:
                seen_stages.add(stage_name)
                await reporter.on_stage(TaskStage(stage_name))
            wlo, whi = STAGE_WINDOWS[TaskStage(stage_name)]
            local = _lo + (_hi - _lo) * max(0.0, min(frac, 1.0))
            await reporter.on_progress(wlo + (whi - wlo) * local)

        computations.append(
            await compute_isochrone(counting, settings, src, levels_min, on_progress=engine_hook)
        )

    isochrone = _merge_iso(computations, levels_min, origin_bd09, entry_point_coords)
    time_fields = [c.field for c in computations]
    if isochrone.degraded_reason is not None:
        # 等时圈估算口径（docs/02 §3.4 第三级）：报告必须显式声明，不冒充实测
        degraded.append(f"isochrone:{isochrone.degraded_reason}")

    # ③ poi：逐类目隔离失败——单类目不可用记降级标记，不拖垮整体。
    #    逐 key 调用 search_categories（其内部即单协程 gather），失败面收敛到单类目。
    await reporter.on_stage(TaskStage.poi)
    taxonomy = load_taxonomy(settings.poi_taxonomy_file)
    per_category = await asyncio.gather(
        *(
            search_categories(
                counting, settings, taxonomy, origin_bd09, [key], settings.poi_search_radius_m
            )
            for key in params.categories
        ),
        return_exceptions=True,
    )
    facilities: dict[str, list[PoiRecord]] = {}
    # 检索失败的类目：盲区不评估、评分不计入——"没有数据"不能被下游当成事实结论
    failed_keys: set[str] = set()
    for key, outcome in zip(params.categories, per_category, strict=True):
        if isinstance(outcome, MapApiError):
            degraded.append(f"poi:{key}:unavailable")
            failed_keys.add(key)
            facilities[key] = []
            log.warning(
                "analysis.poi_degraded", task_id=task_id, category=key, kind=outcome.kind.value
            )
        elif isinstance(outcome, BaseException):
            raise outcome  # 非上游故障（代码缺陷）不吞，直接失败暴露问题
        else:
            facilities.update(outcome)
    await reporter.on_progress(STAGE_WINDOWS[TaskStage.poi][1])

    # ④ coverage：三级漏斗——几何粗筛 → 时间场插值 → 边缘带批量矩阵精判（docs/02 §3.2）。
    #    精判是漏斗中唯一 API 消耗点（≤ verify_limit 个设施，1 次批量矩阵）。
    await reporter.on_stage(TaskStage.coverage)
    labels: dict[str, str] = {spec.key: spec.label for spec in taxonomy}
    threshold_s = params.minutes * 60
    band_s = settings.coverage_edge_band_min * 60
    flat_records = [rec for key in params.categories for rec in facilities.get(key, [])]

    def _estimate(lng: float, lat: float) -> float | None:
        # 多源 min-time：任一入口可达即圈内；全 None（各源均阻挡/无样本）才回落三级精判
        times = [
            f.estimate_seconds(src, lng, lat)
            for f, src in zip(time_fields, origins_bd09, strict=True)
        ]
        valid = [t for t in times if t is not None]
        return min(valid) if valid else None

    # 一级几何 = 各源等时圈的并集（MultiPolygon）：任一源几何包含即圈内
    union_geom = _rings_union(isochrone.levels[-1].coordinates)

    verdicts, edge_indices = classify_facilities(
        flat_records,
        union_geom,
        _estimate,
        threshold_s,
        threshold_s - band_s,
        threshold_s + band_s,
        settings.coverage_field_confidence,
    )
    verified = 0
    if edge_indices:
        chosen = edge_indices[: settings.coverage_matrix_verify_limit]
        destinations = [(flat_records[i].lng, flat_records[i].lat) for i in chosen]
        try:
            # 分批收口在适配器（client.route_matrix 内部按 matrix_batch_size 切批），
            # 编排层一次逻辑调用交付全部目的地——api_call_stats 与等时圈口径一致
            legs = await counting.route_matrix(origin_bd09, destinations)
            verdicts = merge_matrix_verdicts(verdicts, chosen, legs, threshold_s)
            verified = len(chosen)
        except (MapApiError, ValueError) as exc:
            # 降级链（docs/02 §3.4）：矩阵失败 → 逐条规划（第二级）→ 保留插值口径
            # （第三级）。ValueError 兜底：Provider 违反 1:1 返回契约（响应缺行，
            # BF-006）时 merge 的 zip(strict) 抛错——本阶段的承诺是"精判可失败"，
            # 失败面不得击穿整体。
            kind = exc.kind.value if isinstance(exc, MapApiError) else "CONTRACT_VIOLATION"
            walking_legs = await _verify_by_walking(counting, origin_bd09, destinations)
            if walking_legs is None:
                degraded.append("coverage:matrix:unavailable")
                log.warning("analysis.coverage_matrix_degraded", task_id=task_id, kind=kind)
            else:
                verdicts, verified = merge_walking_verdicts(
                    verdicts, chosen, walking_legs, threshold_s
                )
                degraded.append("coverage:matrix:fallback-walking")
                log.warning(
                    "analysis.coverage_walking_fallback",
                    task_id=task_id,
                    kind=kind,
                    measured=verified,
                )
    coverage = CoverageResult(
        threshold_min=params.minutes,
        facilities=verdicts,
        categories=summarize_categories(
            verdicts,
            {key: labels.get(key, key) for key in params.categories},
            settings.coverage_sufficient_count,
        ),
        verified_count=verified,
    )
    await reporter.on_progress(STAGE_WINDOWS[TaskStage.coverage][1])

    # ⑤ blindspot：栅格 + cKDTree + 聚合平滑，全本地零 API（docs/02 §3.3）。
    #    只评估"本次实际检索成功"的类目：未选择/检索失败的类目没有数据，而空设施
    #    列表在栅格语义下是"真实无设施 → 全域缺失"——混入即把数据缺失冒充盲区事实。
    await reporter.on_stage(TaskStage.blindspot)
    searched_keys = set(params.categories) - failed_keys
    blindspot = compute_blindspot(
        origin_bd09,
        facilities,
        settings,
        labels,
        types=[t for t in settings.blindspot_types if t in searched_keys],
    )
    await reporter.on_progress(STAGE_WINDOWS[TaskStage.blindspot][1])

    # 综合评分 = 类目覆盖评分等权平均（公式与口径见 AnalysisReport.overall_score 描述）。
    # 检索失败类目的 0 分是数据缺失而非社区质量，不计入平均；
    # summarize 按 labels（= params.categories 序）产出，strict zip 保证对位。
    category_scores = [
        c.score
        for key, c in zip(params.categories, coverage.categories, strict=True)
        if key not in failed_keys
    ]
    overall_score: float | None = (
        round(sum(category_scores) / len(category_scores), 1) if category_scores else None
    )

    origin_coord = Coord(lng=origin_bd09[0], lat=origin_bd09[1], crs="bd09")
    # 物理口径统计并入 api_call_stats（docs/02 §7.5）：逻辑端点计数之外的
    # 出站 HTTP / 缓存命中 / 重试开销——评审可直接量化，也服务于预算验收断言
    stats: dict[str, int] = dict(counting.counts)
    budget = current_budget()
    if budget is not None:
        stats |= {
            "http_calls": budget.http_calls,
            "cache_hits": budget.cache_hits,
            "retries": budget.retries,
        }
    return AnalysisReport(
        task_id=task_id,
        origin=origin_coord,
        entry_points=entry_point_coords,
        minutes=params.minutes,
        isochrone=isochrone,
        poi=PoiSearchResult(
            origin=origin_coord,
            radius_m=settings.poi_search_radius_m,
            categories=facilities,
        ),
        coverage=coverage,
        blindspot=blindspot,
        overall_score=overall_score,
        degraded_flags=degraded,
        api_call_stats=stats,
        generated_at=datetime.now(UTC),
    )
