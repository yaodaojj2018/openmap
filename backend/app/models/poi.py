"""POI 领域模型。category 由服务层按分类体系回填，Provider 层不感知业务分类。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.common import Coord

CategoryKey = Literal["medical", "education", "shopping", "elderly"]


class PoiRecord(BaseModel):
    uid: str = Field(description="POI 唯一标识（去重键）")
    name: str
    lng: float = Field(description="经度（bd09）")
    lat: float = Field(description="纬度（bd09）")
    address: str = ""
    province: str = ""
    city: str = ""
    area: str = ""
    tag: str = Field(default="", description="POI 标签，用于清洗过滤")
    category: CategoryKey | None = None
    distance_m: float | None = Field(default=None, description="距中心点直线距离（本地 haversine）")


class PoiSearchResult(BaseModel):
    origin: Coord = Field(description="检索中心（bd09）")
    radius_m: int
    categories: dict[str, list[PoiRecord]] = Field(default_factory=dict)
