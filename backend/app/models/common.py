"""通用领域模型：坐标（统一带 crs 声明，内部计算一律先转 BD09）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core import coords as crd

Crs = Literal["bd09", "gcj02", "wgs84"]


class Coord(BaseModel):
    lng: float = Field(ge=73, le=136, description="经度")
    lat: float = Field(ge=3, le=54, description="纬度")
    crs: Crs = "bd09"

    def to_bd09(self) -> tuple[float, float]:
        """转换到内部规范坐标系 BD09（纯本地计算，零 API 成本）。"""
        if self.crs == "bd09":
            return self.lng, self.lat
        if self.crs == "gcj02":
            return crd.gcj02_to_bd09(self.lng, self.lat)
        return crd.wgs84_to_bd09(self.lng, self.lat)
