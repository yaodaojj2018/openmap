"""任务管理器与流水线单测（回放模式）。

覆盖 docs/02 §5.2 核心语义：状态机推进 / 进度单调 / 参数复用省配额 /
单活跃自动取消 / 单类目 POI 降级不拖垮整体 / 上游故障统一转译。
"""

from __future__ import annotations

import asyncio
from itertools import pairwise

from app.core.cache import MemoryCache
from app.core.config import REPO_ROOT, Settings
from app.mapapi.provider import (
    BD09Point,
    ErrorKind,
    MapApiError,
    MapProvider,
)
from app.mapapi.replay.client import ReplayProvider
from app.models.common import Coord
from app.models.geocode import GeocodeCandidate
from app.models.isochrone import RouteLeg
from app.models.poi import PoiRecord
from app.models.task import AnalysisParams, TaskEvent, TaskStage, TaskStatus
from app.tasks.manager import TaskManager

ORIGIN = {"lng": 116.316628, "lat": 39.981909}


def load_replay() -> ReplayProvider:
    return ReplayProvider.from_file(str(REPO_ROOT / "data" / "replays" / "demo.json"))


def make_manager(provider: MapProvider | None = None) -> TaskManager:
    return TaskManager(provider or load_replay(), Settings(demo_mode=True), MemoryCache())


class SlowProvider:
    """包装回放 Provider，每个调用加固定延迟——构造可取消的长任务。"""

    def __init__(self, inner: MapProvider, delay_s: float) -> None:
        self._inner = inner
        self._delay_s = delay_s
        self.name = inner.name

    async def _throttle(self) -> None:
        await asyncio.sleep(self._delay_s)

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        await self._throttle()
        return await self._inner.geocode(address, city)

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        await self._throttle()
        return await self._inner.search_pois(query, center, radius_m, page_size, max_pages)

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        await self._throttle()
        return await self._inner.route_matrix(origin, destinations)

    async def close(self) -> None:
        await self._inner.close()


class FlakyMedicalProvider:
    """仅 medical 类目检索（query=药店）失败——验证单类目降级不拖垮整体。"""

    def __init__(self, inner: MapProvider) -> None:
        self._inner = inner
        self.name = inner.name

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        return await self._inner.geocode(address, city)

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        if query == "药店":
            raise MapApiError(ErrorKind.SERVER, "模拟单类目上游故障")
        return await self._inner.search_pois(query, center, radius_m, page_size, max_pages)

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        return await self._inner.route_matrix(origin, destinations)

    async def close(self) -> None:
        await self._inner.close()


class BrokenMatrixProvider:
    """矩阵调用持续失败——验证等时圈失败 → 任务 failed 且错误文案不裸露上游细节。"""

    def __init__(self) -> None:
        self.name = "broken"

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        return []

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        return []

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        raise MapApiError(ErrorKind.TIMEOUT, "模拟上游持续超时")

    async def close(self) -> None:
        return None


async def test_happy_path_state_machine_and_report() -> None:
    mgr = make_manager()
    task = await mgr.create(AnalysisParams(origin=Coord(**ORIGIN)))
    assert task.status in (TaskStatus.pending, TaskStatus.running)

    final = await mgr.join(task.task_id)
    assert final is not None
    assert final.status is TaskStatus.completed
    assert final.stage is TaskStage.completed
    assert final.progress == 1.0
    assert final.degraded_flags == []
    # 预算口径：阶段 A 1 次 + 二分 ≤3 次 = 4 次矩阵；四大类各 1 次检索
    assert final.api_call_stats["route_matrix"] >= 4
    assert final.api_call_stats["search_pois"] == 4

    report = await mgr.result(task.task_id)
    assert report is not None
    assert [lv.level_min for lv in report.isochrone.levels] == [5, 10, 15]
    assert report.origin.crs == "bd09"
    assert set(report.poi.categories) == {"medical", "education", "shopping", "elderly"}
    assert report.api_call_stats == final.api_call_stats


async def test_event_stream_stage_order_and_progress_monotonic() -> None:
    mgr = make_manager()
    created = await mgr.create(AnalysisParams(origin=Coord(**ORIGIN)))

    events: list[TaskEvent] = []
    async for _seq, event in mgr.events(created.task_id):
        if event is not None:
            events.append(event)

    stages = [ev.stage for ev in events if ev.event == "stage"]
    assert stages == [
        TaskStage.resolving,
        TaskStage.sampling,
        TaskStage.fitting,
        TaskStage.poi,
    ]
    progresses = [ev.progress for ev in events if ev.event == "progress"]
    assert progresses, "应有进度事件"
    assert all(a <= b for a, b in pairwise(progresses)), "进度必须单调"
    assert events[-1].event == "completed"
    assert events[-1].status is TaskStatus.completed


async def test_same_params_reuses_task() -> None:
    mgr = make_manager()
    params = AnalysisParams(origin=Coord(**ORIGIN))
    first = await mgr.create(params)
    second = await mgr.create(params)  # 运行中重复提交：复用同一任务
    assert second.task_id == first.task_id

    await mgr.join(first.task_id)
    third = await mgr.create(params)  # 已完成：直接复用（秒级返回，不重算）
    assert third.task_id == first.task_id
    assert third.status is TaskStatus.completed


async def test_new_task_cancels_active_one() -> None:
    mgr = make_manager(SlowProvider(load_replay(), delay_s=0.05))
    first = await mgr.create(AnalysisParams(origin=Coord(**ORIGIN)))
    second = await mgr.create(
        AnalysisParams(origin=Coord(lng=ORIGIN["lng"] + 0.001, lat=ORIGIN["lat"]))
    )
    assert second.task_id != first.task_id

    final_first = await mgr.join(first.task_id)
    assert final_first is not None
    assert final_first.status is TaskStatus.cancelled, "新任务必须自动取消旧任务"

    final_second = await mgr.join(second.task_id)
    assert final_second is not None
    assert final_second.status is TaskStatus.completed, "新任务不受取消影响"


async def test_poi_single_category_failure_degrades() -> None:
    mgr = make_manager(FlakyMedicalProvider(load_replay()))
    task = await mgr.create(AnalysisParams(origin=Coord(**ORIGIN)))

    final = await mgr.join(task.task_id)
    assert final is not None
    assert final.status is TaskStatus.completed, "单类目失败只降级，不失败"
    assert final.degraded_flags == ["poi:medical:unavailable"]

    report = await mgr.result(task.task_id)
    assert report is not None
    assert report.poi.categories["medical"] == [], "失败类目显式报告 0 个"
    assert report.poi.categories["education"], "其余类目正常"


async def test_isochrone_failure_fails_task_with_translated_error() -> None:
    mgr = make_manager(BrokenMatrixProvider())
    task = await mgr.create(AnalysisParams(origin=Coord(**ORIGIN)))

    final = await mgr.join(task.task_id)
    assert final is not None
    assert final.status is TaskStatus.failed
    assert final.error == "地图服务暂时不可用，请稍后重试", "上游细节不裸露"
    assert await mgr.result(task.task_id) is None


async def test_unknown_task_returns_none() -> None:
    mgr = make_manager()
    assert await mgr.status("no-such-task") is None
    assert await mgr.result("no-such-task") is None
