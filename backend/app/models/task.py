"""分析任务领域模型：状态机、参数与 SSE 事件契约（docs/02 §5.2/§5.3）。

状态（status）与阶段（stage）分离：status 是生命周期粗粒度（运行/终态），
stage 是流水线细粒度位置——SSE 按 stage 推进，轮询兜底看 status。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.common import Coord
from app.models.poi import CategoryKey

DEFAULT_CATEGORIES: tuple[CategoryKey, ...] = ("medical", "education", "shopping", "elderly")


class TaskStatus(StrEnum):
    """任务生命周期（docs/02 §5.2）。降级完成 = completed + degraded_flags。"""

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskStage(StrEnum):
    """流水线阶段（docs/02 §5.2 状态机）。coverage/blindspot 在 M3 后续阶段接入。"""

    pending = "pending"
    resolving = "resolving"
    sampling = "sampling"
    fitting = "fitting"
    poi = "poi"
    coverage = "coverage"
    blindspot = "blindspot"
    completed = "completed"


class AnalysisParams(BaseModel):
    """分析参数。归一化后参与参数哈希：相同参数命中活跃/已完成任务直接复用（省配额）。"""

    origin: Coord = Field(description="代表中心（多源时 = 出入口质心，crs 统一转 bd09）")
    entry_points: list[Coord] = Field(
        default_factory=list,
        max_length=3,
        description="等时圈源点（1~3 个小区出入口；缺省回填 [origin]，单点 = 原行为）",
    )
    minutes: int = Field(default=15, ge=1, le=30, description="步行时长阈值（分钟）")
    categories: list[CategoryKey] = Field(
        default_factory=lambda: list(DEFAULT_CATEGORIES), description="参与的设施大类"
    )

    @model_validator(mode="after")
    def _fill_entry_points(self) -> AnalysisParams:
        """缺省出入口回填 [origin]：单点场景 = 现有行为，零改动。"""
        if not self.entry_points:
            self.entry_points = [self.origin]
        return self


class AnalysisTask(BaseModel):
    """任务快照：创建响应（202）/ /status 响应 / SSE completed 载荷。"""

    task_id: str
    params: AnalysisParams
    status: TaskStatus = TaskStatus.pending
    stage: TaskStage = TaskStage.pending
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="全局进度 0-1")
    degraded_flags: list[str] = Field(
        default_factory=list, description="降级标记（如单类目 POI 失败）"
    )
    api_call_stats: dict[str, int] = Field(
        default_factory=dict, description="按端点计数（geocode/search_pois/route_matrix）"
    )
    created_at: datetime
    error: str | None = None


class TaskEvent(BaseModel):
    """SSE 事件统一载荷：event 字段区分事件类型。

    cancelled 是 docs/02 §5.3 四类事件之外的事务性扩展——单活跃策略会主动取消旧任务，
    前端需将其与 failed 区分（取消不是失败，静默放弃即可）。
    """

    event: Literal["stage", "progress", "completed", "failed", "cancelled"]
    task_id: str
    stage: TaskStage | None = None
    progress: float | None = None
    status: TaskStatus | None = None
    error: str | None = None
    degraded_flags: list[str] | None = None
