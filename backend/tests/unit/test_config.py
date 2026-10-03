"""配置边界单测：v3 地点检索分页契约必须在配置层强制（铁律#4）。

背景（BF-005）：官方 /place/v3/around 的 page_num 仅允许 0、1、2，page_size 取值
10-20；越界页返回 status=0 空结果（静默浪费配额），越界 page_size 被服务端静默
钳制后还会触发客户端"首页即末页"的提前断页。故 Settings 用 Field 约束直接拒绝
越界配置，而不是等运行期静默失真。

本文件全部为历史 Bug 回归（BF-002/003/005，对照表见 docs/regression-ci.md §2）。
默认值断言统一 _env_file=None 并清除相关环境变量：回归口径必须是"代码默认值"，
不能被本地 .env 的合法覆盖搅成环境相关。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import REPO_ROOT, Settings

pytestmark = pytest.mark.regression


def _isolated_env(monkeypatch: pytest.MonkeyPatch, *vars: str) -> None:
    """默认值断言前置：清掉可能覆盖目标字段的环境变量。"""
    for var in vars:
        monkeypatch.delenv(var, raising=False)


def test_poi_pagination_defaults_match_v3_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认值对齐 v3 契约：单页 20（v3 上限）、最多 3 页（page_num 0-2）。"""
    _isolated_env(monkeypatch, "OPENMAP_POI_PAGE_SIZE", "OPENMAP_POI_MAX_PAGES")
    s = Settings(demo_mode=True, _env_file=None)
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


def test_env_file_anchored_to_repo_root() -> None:
    """回归（BF-002）：env_file 必须锚定仓库根的绝对路径，与进程 cwd 无关。

    曾为相对路径 ".env"，uvicorn 从 backend/ 启动时实际读 backend/.env（不存在），
    根目录密钥全部静默失效。此处钉住"绝对路径 + 指向仓库根"两个属性，
    谁改回相对路径立即红。
    """
    env_file = Path(Settings.model_config["env_file"])
    assert env_file.is_absolute()
    assert env_file == REPO_ROOT / ".env"


def test_rate_limit_defaults_match_free_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归（BF-003）：默认限流必须压在免费档"地点检索并发 3"契约之内。

    曾默认 qps=5 / burst=10，四类目 asyncio.gather 瞬时并发 4 触发百度告警短信。
    默认值是契约的镜像：若官方放宽并发，应连本测试与 docs/02 一并更新，
    而不是只改数字。
    """
    _isolated_env(monkeypatch, "OPENMAP_API_QPS", "OPENMAP_API_BURST")
    s = Settings(demo_mode=True, _env_file=None)
    assert s.api_qps == 2.0
    assert s.api_burst == 3.0
