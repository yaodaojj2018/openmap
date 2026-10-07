"""分析任务路由（docs/02 §5.3 契约）。

SSE 用原生 StreamingResponse 手写 text/event-stream（无第三方 SSE 依赖）：
15s 心跳注释帧防代理断连，Last-Event-ID 头支持断线续传（回放事件环形缓冲）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_task_manager
from app.models.common import Coord
from app.models.poi import CategoryKey
from app.models.report import AnalysisReport
from app.models.task import (
    DEFAULT_CATEGORIES,
    AnalysisParams,
    AnalysisTask,
    TaskStatus,
)
from app.tasks.manager import TERMINAL_EVENTS, TaskManager

router = APIRouter(tags=["analyses"])

_SSE_HEARTBEAT_S = 15.0


class AnalysisRequest(BaseModel):
    """POST /analyses 请求体（docs/02 §5.3）。"""

    origin: Coord = Field(description="中心点（crs 可选 bd09/gcj02/wgs84，内部统一转 bd09）")
    minutes: int = Field(default=15, ge=1, le=30, description="步行时长阈值（分钟）")
    categories: list[CategoryKey] = Field(
        default_factory=list, description="设施大类，空 = 四大类全选"
    )


@router.post("/analyses", response_model=AnalysisTask, status_code=202, summary="创建分析任务")
async def create_analysis(
    body: AnalysisRequest,
    manager: Annotated[TaskManager, Depends(get_task_manager)],
) -> AnalysisTask:
    """创建异步分析任务；同参数复用既有任务，新中心点自动取消旧任务。"""
    params = AnalysisParams(
        origin=body.origin,
        minutes=body.minutes,
        categories=body.categories or list(DEFAULT_CATEGORIES),
    )
    return await manager.create(params)


@router.get(
    "/analyses/{task_id}/status",
    response_model=AnalysisTask,
    summary="任务状态（SSE 不可用时轮询兜底）",
)
async def task_status(
    task_id: str,
    manager: Annotated[TaskManager, Depends(get_task_manager)],
) -> AnalysisTask:
    task = await manager.status(task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在或已过期"}
        )
    return task


@router.get("/analyses/{task_id}/result", response_model=AnalysisReport, summary="完整分析报告")
async def task_result(
    task_id: str,
    manager: Annotated[TaskManager, Depends(get_task_manager)],
) -> AnalysisReport:
    task = await manager.status(task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在或已过期"}
        )
    if task.status is not TaskStatus.completed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TASK_NOT_COMPLETED",
                "message": f"任务未完成（当前状态 {task.status.value}）",
            },
        )
    report = await manager.result(task_id)
    if report is None:  # 理论不可达（completed 必有报告），防御性兜底
        raise HTTPException(
            status_code=409,
            detail={"code": "TASK_NOT_COMPLETED", "message": "报告不可用，请重新发起分析"},
        )
    return report


@router.get(
    "/analyses/{task_id}/events",
    summary="SSE 事件流（stage/progress/completed/failed/cancelled）",
)
async def task_events(
    task_id: str,
    manager: Annotated[TaskManager, Depends(get_task_manager)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    task = await manager.status(task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在或已过期"}
        )
    try:
        last_seq = int(last_event_id) if last_event_id else 0
    except ValueError:
        last_seq = 0

    async def stream() -> AsyncIterator[str]:
        async for seq, event in manager.events(task_id, last_seq, _SSE_HEARTBEAT_S):
            if event is None:
                yield ": ping\n\n"  # 注释帧：保活且不被 EventSource 当作事件
                continue
            data = event.model_dump_json(exclude_none=True)
            yield f"id: {seq}\nevent: {event.event}\ndata: {data}\n\n"
            if event.event in TERMINAL_EVENTS:
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        # X-Accel-Buffering: no —— nginx 反代（compose 部署）不缓冲事件流
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
