"""地理编码领域模型。坐标一律 BD09（百度地理编码默认返回 bd09ll）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GeocodeCandidate(BaseModel):
    address: str = Field(description="格式化地址")
    lng: float = Field(description="经度（bd09）")
    lat: float = Field(description="纬度（bd09）")
    level: str = Field(default="", description="解析级别，如 门址/道路/地标")
    city: str = Field(default="", description="所属城市")
    precise: int | None = Field(default=None, description="是否精确解析：1 精确 / 0 模糊")
    confidence: int | None = Field(default=None, description="可信度 0-100")


class GeocodeResult(BaseModel):
    candidates: list[GeocodeCandidate] = Field(default_factory=list)
