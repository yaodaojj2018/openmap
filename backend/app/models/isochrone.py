"""等时圈领域模型。坐标均为 BD09；多边形为 GeoJSON Polygon 坐标结构。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import Coord


class RouteLeg(BaseModel):
    """一对 OD 的步行测量结果。不可达时 distance/duration 均为 None。"""

    distance_m: float | None = None
    duration_s: float | None = None

    @property
    def reachable(self) -> bool:
        return self.duration_s is not None


class IsochroneLevel(BaseModel):
    """单级等时圈：level_min 分钟可达边界。"""

    level_min: int
    coordinates: list[list[list[float]]] = Field(
        description="GeoJSON Polygon rings：[ [ [lng,lat], ... ] ]（外环，必要时含内环）"
    )
    area_km2: float
    confidence: float = Field(ge=0, le=1, description="方向覆盖率×采样质量综合置信度")


class IsochroneResult(BaseModel):
    """等时圈计算结果（v1：射线插值 + 闭合样条，见 docs/02 §10 M2 裁剪注）。"""

    origin: Coord
    levels: list[IsochroneLevel]
    probe_count: int = Field(description="总探测点数（含二分细化）")
    matrix_batches: int = Field(description="批量矩阵调用次数（API 成本口径）")
    method: str = "ray-spline-v1"
    degraded_reason: str | None = Field(
        default=None,
        description=(
            "降级原因：matrix:unavailable / budget:exhausted——矩阵失败后"
            "改用直线×1.3 模型估算（docs/02 §3.4 第三级），method 同步切换"
        ),
    )
