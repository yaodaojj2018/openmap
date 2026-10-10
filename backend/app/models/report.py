"""分析报告模型：流水线各阶段产物的组装容器。

M3 阶段逐步生长：当前含等时圈 + POI 清单 + 覆盖判定 + 盲区 + 综合评分，字段只增不改
——保证已上线响应结构稳定。coverage/blindspot/overall_score 为 Optional：升级前持久化
的旧报告（TTL 24h 缓存）缺这些字段仍可反序列化。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.blindspot import BlindspotResult
from app.models.common import Coord
from app.models.coverage import CoverageResult
from app.models.isochrone import IsochroneResult
from app.models.poi import PoiSearchResult


class AnalysisReport(BaseModel):
    """完整分析报告（/analyses/{id}/result 响应）。坐标均为 BD09。"""

    task_id: str
    origin: Coord = Field(description="规范化后的中心点（bd09，多源时 = 出入口质心）")
    entry_points: list[Coord] = Field(
        default_factory=list, description="等时圈源点（1~3 个小区出入口，前端据此重绘标记）"
    )
    minutes: int = Field(description="步行时长阈值（分钟）")
    isochrone: IsochroneResult = Field(description="多级等时圈（v1 射线样条）")
    poi: PoiSearchResult = Field(description="分类设施清单（单类目失败时该类为空列表）")
    coverage: CoverageResult | None = Field(
        default=None, description="三级漏斗覆盖判定（M3 接入；旧缓存报告可能缺省）"
    )
    blindspot: BlindspotResult | None = Field(
        default=None, description="栅格盲区识别（M3 接入；旧缓存报告可能缺省）"
    )
    overall_score: float | None = Field(
        default=None,
        description=(
            "综合评分（0-100）= 各类目覆盖评分的等权平均（文档未定权重，docs/01 §147"
            "仅要求分类评分）；POI 检索失败的类目不计入平均（数据缺失≠社区质量）；"
            "coverage 缺省（旧缓存报告）时为 None"
        ),
    )
    degraded_flags: list[str] = Field(default_factory=list)
    api_call_stats: dict[str, int] = Field(default_factory=dict, description="按端点计数")
    generated_at: datetime
