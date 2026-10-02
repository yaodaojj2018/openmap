"""配置边界单测：v3 地点检索分页契约必须在配置层强制（铁律#4）。

背景（BF-005）：官方 /place/v3/around 的 page_num 仅允许 0、1、2，page_size 取值
10-20；越界页返回 status=0 空结果（静默浪费配额），越界 page_size 被服务端静默
钳制后还会触发客户端"首页即末页"的提前断页。故 Settings 用 Field 约束直接拒绝
越界配置，而不是等运行期静默失真。
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_poi_pagination_defaults_match_v3_contract() -> None:
    """默认值对齐 v3 契约：单页 20（v3 上限）、最多 3 页（page_num 0-2）。"""
    s = Settings(demo_mode=True)
    assert s.poi_page_size == 20
    assert s.poi_max_pages == 3


@pytest.mark.parametrize("bad_page_size", [9, 21, 30])
def test_poi_page_size_out_of_range_rejected(bad_page_size: int) -> None:
    """page_size 越界（如想当然设 30）必须启动即报错，不能被服务端静默钳到 20。"""
    with pytest.raises(ValidationError, match="poi_page_size"):
        Settings(demo_mode=True, poi_page_size=bad_page_size)


@pytest.mark.parametrize("bad_max_pages", [0, 4, 5])
def test_poi_max_pages_out_of_range_rejected(bad_max_pages: int) -> None:
    """max_pages 越界（如沿用 v2 时代的 5）必须启动即报错：page_num=3/4 只会拿到
    status=0 空页，白耗配额且无任何报错。"""
    with pytest.raises(ValidationError, match="poi_max_pages"):
        Settings(demo_mode=True, poi_max_pages=bad_max_pages)
