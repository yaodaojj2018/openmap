"""分析报告模型：流水线各阶段产物的组装容器。

M3 阶段逐步生长：当前含等时圈 + POI 清单；coverage/blindspot/评分（HealthReport
完整形态，docs/02 §5.4）在后续阶段注入，字段只增不改——保证已上线响应结构稳定。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import Coord
from app.models.isochrone import IsochroneResult
from app.models.poi import PoiSearchResult


class AnalysisReport(BaseModel):
    """完整分析报告（/analyses/{id}/result 响应）。坐标均为 BD09。"""

    task_id: str
    origin: Coord = Field(description="规范化后的中心点（bd09）")
    minutes: int = Field(description="步行时长阈值（分钟）")
    isochrone: IsochroneResult = Field(description="多级等时圈（v1 射线样条）")
    poi: PoiSearchResult = Field(description="分类设施清单（单类目失败时该类为空列表）")
    degraded_flags: list[str] = Field(default_factory=list)
    api_call_stats: dict[str, int] = Field(default_factory=dict, description="按端点计数")
    generated_at: datetime
