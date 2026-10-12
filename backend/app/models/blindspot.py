"""盲区识别领域模型（docs/02 §3.3）。

口径声明：距离为栅格中心到最近设施的**直线距离**（haversine，命题"1 km"字面口径）；
路网测时口径的差异为文档声明的可选增强，不在本模型中。
坐标均为 BD09。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import Coord


class BlindspotTypeResult(BaseModel):
    """单类关键设施的盲区识别结果。"""

    type_key: str = Field(description="设施类目键（medical/education/shopping）")
    label: str
    missing_cells: int = Field(ge=0, description="缺失该类的栅格数")
    worst_distance_m: float | None = Field(
        default=None, description="缺失栅格中最远的最近设施距离（该类完全无设施为 None）"
    )
    polygons: list[list[list[list[float]]]] = Field(
        default_factory=list,
        description="盲区多边形（GeoJSON Polygon 环组：[外环, 内环...]，bd09），buffer 平滑后",
    )


class BlindspotResult(BaseModel):
    """盲区识别汇总（/analyses/{id}/result 的 blindspot 字段）。"""

    origin: Coord
    extent_m: int = Field(description="研究区域边长（米）")
    cell_size_m: int = Field(description="栅格分辨率（米）")
    grid_side: int = Field(description="单边栅格数（grid_side² = 总栅格数）")
    threshold_m: int = Field(description="超此距离判缺失（命题口径 1000m）")
    types: list[BlindspotTypeResult] = Field(default_factory=list)
    max_severity: int = Field(ge=0, description="单栅格最多同时缺失的类数（0..len(types)）")
    severe_cells: int = Field(ge=0, description="同时缺失 ≥2 类的复合盲区栅格数")
    mask_enabled: bool = Field(default=False, description="是否应用居住掩膜（正向识别居住用地）")
    residential_cells: int = Field(
        default=0, ge=0, description="居住掩膜覆盖的栅格数（未启用掩膜时 = 总栅格数）"
    )
    excluded_cells: int = Field(
        default=0, ge=0, description="非居住用地排除的栅格数（未启用掩膜时 = 0）"
    )
    industrial_cells: int = Field(
        default=0, ge=0, description="工业园负向修正扣除的栅格数（居住格中落入工业园半径内的）"
    )
