"""分析流水线编排（docs/02 §5.1 时序 / §5.2 状态机）。

职责边界：把 Provider 注入各域层入口并推进阶段与进度；降级决策收口在此层
（域层保持纯函数/纯编排，铁律 #2）。api_call_stats 由 CountingProvider 在协议层计数。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol

import structlog

from app.core.config import Settings
from app.isochrone.engine import compute_isochrone
from app.mapapi.provider import BD09Point, MapApiError, MapProvider
from app.models.common import Coord
from app.models.geocode import GeocodeCandidate
from app.models.isochrone import IsochroneResult, RouteLeg
from app.models.poi import PoiRecord, PoiSearchResult
from app.models.report import AnalysisReport
from app.models.task import AnalysisParams, TaskStage
from app.poi.service import load_taxonomy, search_categories

# 各阶段的全局进度窗口（docs/02 §5.1：等时圈 30%→70%、POI 70%→85%……）。
# coverage/blindspot 窗口先行登记，M3 后续阶段接入编排时即生效。
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

    async def close(self) -> None:
        await self.inner.close()


def resolve_levels(minutes: int, settings: Settings) -> list[int]:
    """阈值分钟 → 多级圈级别：配置级别中 < 阈值者 + 阈值本身（如 15 → [5, 10, 15]）。"""
    return sorted({m for m in settings.isochrone_levels_min if m < minutes} | {minutes})


async def run_analysis(
    task_id: str,
    params: AnalysisParams,
    provider: MapProvider,
    settings: Settings,
    reporter: TaskReporter,
) -> AnalysisReport:
    """执行完整分析流水线，产出报告（含降级标记与 API 统计）。

    失败语义：等时圈失败 = 整体失败（无圈则无报告）；单类目 POI 失败 = 降级标记
    继续（docs/02 §3.4：POI 为空显式报告"该类 0 个"而非报错）。
    """
    counting = CountingProvider(provider)
    degraded: list[str] = []
    log = structlog.get_logger(__name__)

    # ① resolving：坐标规范化。地址解析在创建前的 /geocode 完成（docs/02 §5.1 时序），
    #    任务内只做 crs → bd09 纯本地转换，不消耗 API。
    await reporter.on_stage(TaskStage.resolving)
    origin_bd09 = params.origin.to_bd09()
    await reporter.on_progress(STAGE_WINDOWS[TaskStage.resolving][1])

    # ② sampling + fitting：engine 的本地阶段回调 → 全局进度窗口映射
    await reporter.on_stage(TaskStage.sampling)
    seen_stages: set[str] = {"sampling"}  # sampling 已上报，避免 engine 首次回调重复推进

    async def engine_hook(stage_name: str, frac: float) -> None:
        if stage_name not in seen_stages:
            seen_stages.add(stage_name)
            await reporter.on_stage(TaskStage(stage_name))
        lo, hi = STAGE_WINDOWS[TaskStage(stage_name)]
        await reporter.on_progress(lo + (hi - lo) * max(0.0, min(frac, 1.0)))

    isochrone: IsochroneResult = await compute_isochrone(
        counting,
        settings,
        origin_bd09,
        resolve_levels(params.minutes, settings),
        on_progress=engine_hook,
    )

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
    for key, outcome in zip(params.categories, per_category, strict=True):
        if isinstance(outcome, MapApiError):
            degraded.append(f"poi:{key}:unavailable")
            facilities[key] = []
            log.warning(
                "analysis.poi_degraded", task_id=task_id, category=key, kind=outcome.kind.value
            )
        elif isinstance(outcome, BaseException):
            raise outcome  # 非上游故障（代码缺陷）不吞，直接失败暴露问题
        else:
            facilities.update(outcome)
    await reporter.on_progress(STAGE_WINDOWS[TaskStage.poi][1])

    origin_coord = Coord(lng=origin_bd09[0], lat=origin_bd09[1], crs="bd09")
    return AnalysisReport(
        task_id=task_id,
        origin=origin_coord,
        minutes=params.minutes,
        isochrone=isochrone,
        poi=PoiSearchResult(
            origin=origin_coord,
            radius_m=settings.poi_search_radius_m,
            categories=facilities,
        ),
        degraded_flags=degraded,
        api_call_stats=dict(counting.counts),
        generated_at=datetime.now(UTC),
    )
