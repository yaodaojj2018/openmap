"""覆盖判定领域模型（docs/02 §3.2 三级漏斗的产出契约）。

口径声明：est_walk_time_min 为"直线时间场插值估算"或"批量矩阵实测"两种来源之一，
由 method 标注；圈外设施的耗时不做估算（几何口径已定论）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CoverageMethod(StrEnum):
    """判定来源（漏斗层级），驱动置信度与前端样式。"""

    POLYGON = "polygon"  # 一级：几何粗筛，圈外定论（确定）
    FIELD = "field"  # 二级：时间场插值估算（置信度配置化，默认 0.8）
    MATRIX = "matrix"  # 三级：边缘带批量矩阵实测（确定）


class FacilityCoverage(BaseModel):
    """单设施覆盖判定结果。"""

    uid: str
    name: str
    category: str
    est_walk_time_min: float | None = Field(
        default=None, description="估算/实测步行分钟；圈外、实测不可达或阻挡方向未估算为 None"
    )
    in_circle: bool = Field(description="是否在阈值分钟等时圈内可达")
    method: CoverageMethod
    confidence: float = Field(ge=0, le=1)


class CategoryCoverage(BaseModel):
    """类目覆盖统计（体检报告评分数据源，docs/01 §6 交付：数量/覆盖率/平均耗时/评分）。

    评分公式：score = 100 × (0.6 × coverage_ratio + 0.4 × min(1, reachable / 充足阈值))，
    兼顾"可达率"与"数量充足度"（单一设施 100% 可达不足以支撑社区韧性）；
    充足阈值默认 5，见 Settings.coverage_sufficient_count。
    """

    category: str
    label: str
    total: int = Field(ge=0)
    reachable: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1, description="reachable / total（total=0 记 0）")
    avg_walk_time_min: float | None = Field(
        default=None, description="可达设施的平均步行分钟（无可达设施为 None）"
    )
    score: float = Field(ge=0, le=100)


class CoverageResult(BaseModel):
    """覆盖判定汇总（/analyses/{id}/result 的 coverage 字段）。"""

    threshold_min: int = Field(description="判定阈值（分钟）")
    facilities: list[FacilityCoverage] = Field(default_factory=list)
    categories: list[CategoryCoverage] = Field(default_factory=list)
    verified_count: int = Field(default=0, ge=0, description="边缘带矩阵实测数（漏斗三级实际消耗）")
