"""异步任务管理器：状态机 + 事件扇出 + 取消与参数复用（docs/02 §5.2）。

部署形态取舍：任务态以进程内注册表为事实源（单 uvicorn worker 部署）；
终态与报告写穿 CacheBackend——有 Redis 时跨重启可恢复（TTL 可配），无则内存态。
SSE 扇出用订阅者队列 + 每任务事件环形缓冲：CacheBackend 无 pub/sub，
不能借用缓存做事件通道。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from app.core.cache import CacheBackend, cache_key
from app.core.config import Settings
from app.core.logging import bind_trace
from app.isochrone.engine import IsochroneError
from app.mapapi.provider import MapApiError, MapProvider
from app.models.report import AnalysisReport
from app.models.task import (
    AnalysisParams,
    AnalysisTask,
    TaskEvent,
    TaskStage,
    TaskStatus,
)
from app.tasks.pipeline import run_analysis

_EVENT_BUFFER = 256  # SSE 断线重放缓冲上限（事件量级：每任务数十条）
_REGISTRY_LIMIT = 50  # 进程内保留的任务数；更早的靠缓存（有 Redis 时）恢复
TERMINAL_EVENTS = ("completed", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(UTC)


def params_hash(params: AnalysisParams) -> str:
    """参数归一化哈希：坐标取整 6 位小数（≈0.1m 精度）+ 类目排序（docs/02 §5.2 复用口径）。

    出入口集合参与哈希（排序消除顺序差异）：不同出入口组合不得命中同一缓存复用。
    """
    lng, lat = params.origin.to_bd09()
    entry_points = ",".join(
        f"{round(elng, 6)},{round(elat, 6)}"
        for elng, elat in sorted(c.to_bd09() for c in params.entry_points)
    )
    return cache_key(
        "analysis-params",
        lng=round(lng, 6),
        lat=round(lat, 6),
        entry_points=entry_points,
        minutes=params.minutes,
        categories=",".join(sorted(params.categories)),
    )


@dataclass
class _TaskState:
    """任务运行态：快照模型 + 事件缓冲 + 订阅者队列（均进程内，单事件_loop 免锁）。"""

    task: AnalysisTask
    events: deque[tuple[int, TaskEvent]] = field(
        default_factory=lambda: deque(maxlen=_EVENT_BUFFER)
    )
    subscribers: list[asyncio.Queue[tuple[int, TaskEvent] | None]] = field(default_factory=list)
    report: AnalysisReport | None = None
    runner: asyncio.Task[None] | None = None
    seq: int = 0


class _Reporter:
    """pipeline 进度回调 → 状态机落档 + 事件扇出（manager 私有桥）。"""

    def __init__(self, manager: TaskManager, state: _TaskState) -> None:
        self._manager = manager
        self._state = state

    async def on_stage(self, stage: TaskStage) -> None:
        task = self._state.task
        task.stage = stage
        if task.status is TaskStatus.pending:
            task.status = TaskStatus.running
        self._manager._emit(
            self._state,
            TaskEvent(
                event="stage",
                task_id=task.task_id,
                stage=stage,
                progress=task.progress,
                status=task.status,
            ),
        )
        await self._manager._persist(self._state)  # 阶段推进持久化（断线恢复可见）

    async def on_progress(self, progress: float) -> None:
        task = self._state.task
        if progress <= task.progress:
            return  # 进度单调递增，忽略回摆
        task.progress = min(progress, 0.99)  # 1.0 只属于 completed
        self._manager._emit(
            self._state,
            TaskEvent(
                event="progress",
                task_id=task.task_id,
                stage=task.stage,
                progress=task.progress,
            ),
        )


class TaskManager:
    """分析任务管理器（挂 app.state，生命周期 = 应用生命周期）。

    单活跃策略（docs/02 §5.2 竞态控制）：同一时刻仅一个任务运行，
    新任务自动取消旧任务；前端无需显式取消。
    """

    def __init__(self, provider: MapProvider, settings: Settings, cache: CacheBackend) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache
        self._tasks: dict[str, _TaskState] = {}
        self._params_index: dict[str, str] = {}  # 参数哈希 → task_id（复用判定）
        self._order: deque[str] = deque()  # 创建顺序，用于注册表容量淘汰
        self._active: str | None = None
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(__name__)

    # ---- 对外接口 ----

    async def create(self, params: AnalysisParams) -> AnalysisTask:
        """创建任务并立即返回（202 语义）。

        同参数命中活跃/已完成任务直接复用（省配额）；单活跃策略自动取消旧任务。
        """
        phash = params_hash(params)
        async with self._lock:
            existing_id = self._params_index.get(phash)
            if existing_id:
                state = self._tasks.get(existing_id)
                if state is not None and state.task.status in (
                    TaskStatus.pending,
                    TaskStatus.running,
                    TaskStatus.completed,
                ):
                    if self._active and self._active != existing_id:
                        await self._cancel_locked(self._active, reason="superseded")
                    self._active = (
                        existing_id if state.task.status != TaskStatus.completed else None
                    )
                    return state.task.model_copy()

            task_id = uuid.uuid4().hex[:12]
            state = _TaskState(
                task=AnalysisTask(
                    task_id=task_id,
                    params=params.model_copy(deep=True),
                    created_at=_now(),
                )
            )
            self._tasks[task_id] = state
            self._params_index[phash] = task_id
            self._order.append(task_id)
            self._prune_locked()
            if self._active and self._active != task_id:
                await self._cancel_locked(self._active, reason="superseded")
            self._active = task_id
            state.runner = asyncio.create_task(self._run(state), name=f"analysis:{task_id}")
            return state.task.model_copy()

    async def join(self, task_id: str) -> AnalysisTask | None:
        """等待任务结束并返回终态快照（测试与排错用）。"""
        state = self._tasks.get(task_id)
        if state is not None and state.runner is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await state.runner  # 取消语义已由状态机表达
        return await self.status(task_id)

    async def status(self, task_id: str) -> AnalysisTask | None:
        """任务快照；进程内未命中时尝试从缓存恢复（重启场景）。"""
        state = self._tasks.get(task_id)
        if state is None:
            state = await self._load_persisted(task_id)
        return state.task.model_copy() if state is not None else None

    async def result(self, task_id: str) -> AnalysisReport | None:
        """完整报告（仅 completed 任务有值，由路由层校验状态）。"""
        state = self._tasks.get(task_id)
        if state is None:
            state = await self._load_persisted(task_id)
        return state.report if state is not None else None

    async def events(
        self, task_id: str, last_seq: int = 0, heartbeat_s: float = 15.0
    ) -> AsyncIterator[tuple[int, TaskEvent | None]]:
        """SSE 事件流：先回放缓冲（Last-Event-ID 断线续传），再订阅实时事件。

        终态事件后自然结束；长时间无事件时产出 (0, None) 心跳哨兵，
        路由层转成 ": ping" 注释帧——保活且不污染事件流。
        """
        state = self._tasks.get(task_id)
        if state is None:
            state = await self._load_persisted(task_id)
        if state is None:
            return
        for seq, event in list(state.events):
            if seq > last_seq:
                yield seq, event
        if state.task.status not in (TaskStatus.pending, TaskStatus.running):
            return  # 终态任务：回放完缓冲即结束
        queue: asyncio.Queue[tuple[int, TaskEvent] | None] = asyncio.Queue()
        state.subscribers.append(queue)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                except TimeoutError:
                    yield 0, None
                    continue
                if item is None:
                    return
                seq, event = item
                if seq <= last_seq or event is None:
                    continue
                yield seq, event
                if event.event in TERMINAL_EVENTS:
                    return
        finally:
            if queue in state.subscribers:
                state.subscribers.remove(queue)

    async def aclose(self) -> None:
        """应用关停：运行中任务标记取消并等待收尾（lifespan shutdown 调用）。"""
        runners = [
            state.runner
            for state in self._tasks.values()
            if state.runner is not None and not state.runner.done()
        ]
        for state in self._tasks.values():
            if state.task.status in (TaskStatus.pending, TaskStatus.running):
                state.task.status = TaskStatus.cancelled
                self._emit(
                    state,
                    TaskEvent(
                        event="cancelled",
                        task_id=state.task.task_id,
                        status=TaskStatus.cancelled,
                    ),
                )
        for runner in runners:
            runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)

    # ---- 内部实现 ----

    def _emit(self, state: _TaskState, event: TaskEvent) -> int:
        """事件编号入缓冲并扇出到订阅者（同步非阻塞，事件循环内原子）。"""
        state.seq += 1
        state.events.append((state.seq, event))
        for queue in list(state.subscribers):
            queue.put_nowait((state.seq, event))
        return state.seq

    async def _persist(self, state: _TaskState) -> None:
        """任务快照（含报告，若有）写穿缓存：analysis:{id}，TTL 可配。

        持久化失败只告警不阻断——缓存是恢复通道而非事实源（铁律 #3 的容错取向）。
        """
        payload = json.dumps(
            {
                "task": state.task.model_dump(mode="json"),
                "report": state.report.model_dump(mode="json") if state.report else None,
            },
            ensure_ascii=False,
        )
        try:
            await self._cache.set(
                f"analysis:{state.task.task_id}", payload, self._settings.task_ttl_s
            )
        except Exception:
            self._log.warning("analysis.persist_failed", task_id=state.task.task_id)

    async def _load_persisted(self, task_id: str) -> _TaskState | None:
        """从缓存恢复任务（进程重启场景）。

        运行中的陈旧快照标记 failed——原进程已消失，任务不可能继续推进，
        显式失败优于永远 running（刷新页面/重连后用户能立刻重新发起）。
        """
        raw = await self._cache.get(f"analysis:{task_id}")
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            task = AnalysisTask.model_validate(data["task"])
            report = AnalysisReport.model_validate(data["report"]) if data.get("report") else None
        except (ValueError, KeyError):
            return None
        if task.status in (TaskStatus.pending, TaskStatus.running):
            task = task.model_copy(
                update={"status": TaskStatus.failed, "error": "任务因服务重启中断，请重新发起"}
            )
        state = _TaskState(task=task, report=report)
        self._tasks[task_id] = state
        self._order.append(task_id)
        self._prune_locked()
        return state

    async def _cancel_locked(self, task_id: str, reason: str) -> None:
        """取消任务（调用方持有锁）。先落状态再 cancel runner，避免竞态双发事件。"""
        state = self._tasks.get(task_id)
        if state is None or state.task.status not in (TaskStatus.pending, TaskStatus.running):
            return
        state.task.status = TaskStatus.cancelled
        await self._persist(state)
        self._emit(
            state,
            TaskEvent(event="cancelled", task_id=task_id, status=TaskStatus.cancelled),
        )
        self._log.info("analysis.cancelled", task_id=task_id, reason=reason)
        if state.runner is not None and not state.runner.done():
            state.runner.cancel()

    def _prune_locked(self) -> None:
        """控制注册表规模：淘汰最老的非活跃任务（其终态副本仍在缓存中）。"""
        while len(self._order) > _REGISTRY_LIMIT:
            oldest = self._order[0]
            if oldest == self._active:
                break
            self._order.popleft()
            self._tasks.pop(oldest, None)
            for key in [k for k, v in self._params_index.items() if v == oldest]:
                del self._params_index[key]

    async def _fail(self, state: _TaskState, message: str) -> None:
        state.task.status = TaskStatus.failed
        state.task.error = message
        await self._persist(state)
        self._emit(
            state,
            TaskEvent(
                event="failed",
                task_id=state.task.task_id,
                status=TaskStatus.failed,
                error=message,
            ),
        )

    async def _run(self, state: _TaskState) -> None:
        """任务主体：执行流水线并驱动状态机到终态（manager 内部）。"""
        bind_trace(state.task.task_id)  # 任务级 trace 贯穿日志（core/logging）
        task = state.task
        try:
            report = await run_analysis(
                task.task_id, task.params, self._provider, self._settings, _Reporter(self, state)
            )
            state.report = report
            task.stage = TaskStage.completed
            task.progress = 1.0
            task.degraded_flags = report.degraded_flags
            task.api_call_stats = report.api_call_stats
            task.status = TaskStatus.completed
            await self._persist(state)
            self._emit(
                state,
                TaskEvent(
                    event="completed",
                    task_id=task.task_id,
                    stage=TaskStage.completed,
                    progress=1.0,
                    status=TaskStatus.completed,
                    degraded_flags=report.degraded_flags,
                ),
            )
            self._log.info(
                "analysis.completed",
                task_id=task.task_id,
                degraded=report.degraded_flags,
                api_calls=report.api_call_stats,
            )
        except asyncio.CancelledError:
            # 取消意图已由 _cancel_locked/aclose 落档并扇出，此处只做幂等兜尾；
            # 吞掉取消是刻意取舍：状态机归管理器独占，runner 不再向上传播
            if task.status not in (TaskStatus.cancelled, TaskStatus.failed):
                task.status = TaskStatus.cancelled
                self._emit(
                    state,
                    TaskEvent(event="cancelled", task_id=task.task_id, status=TaskStatus.cancelled),
                )
        except IsochroneError as exc:
            await self._fail(state, f"等时圈参数非法：{exc}")
        except MapApiError as exc:
            # 上游异常不裸露给前端（docs/02 §5.3）：细节进日志，统一转译文案
            self._log.warning("analysis.mapapi_failed", task_id=task.task_id, kind=exc.kind.value)
            await self._fail(state, "地图服务暂时不可用，请稍后重试")
        except Exception as exc:
            self._log.exception("analysis.crashed", task_id=task.task_id)
            await self._fail(state, f"分析任务执行失败：{exc}")
        finally:
            if self._active == task.task_id:
                self._active = None
            for queue in list(state.subscribers):
                queue.put_nowait(None)  # 通知 SSE 生成器收尾（终态事件已发）
