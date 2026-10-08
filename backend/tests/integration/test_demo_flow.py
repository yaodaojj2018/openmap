"""回放模式端到端测试：M1 验收标准的后端部分——"输入地址出坐标，分类 POI 可检索"。

整个文件为主链路冒烟（smoke）：任何 PR 的第一道测试门禁（docs/regression-ci.md §5）。
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import REPO_ROOT, Settings
from app.main import create_app
from app.mapapi.replay.client import ReplayProvider

pytestmark = pytest.mark.smoke


def make_demo_app() -> TestClient:
    app = create_app(Settings(demo_mode=True))
    return TestClient(app)


def load_provider() -> ReplayProvider:
    return ReplayProvider.from_file(str(REPO_ROOT / "data" / "replays" / "demo.json"))


async def test_replay_geocode_match() -> None:
    provider = load_provider()
    exact = await provider.geocode("北京市海淀区中关村大街1号")
    assert exact and exact[0].lng == 116.316628
    fuzzy = await provider.geocode("我要找中关村街道附近")
    assert fuzzy and fuzzy[0].level == "地标"
    miss = await provider.geocode("不存在的地址xyz")
    assert miss == []


async def test_replay_poi_radius_and_noise() -> None:
    provider = load_provider()
    center = (116.316628, 39.981909)
    medical = await provider.search_pois("药店", center, 1300, 20, 5)
    # 远郊条目（~1.7km）被半径过滤
    assert all(p.uid != "demo-med-far" for p in medical)
    assert len(medical) == 4

    education = await provider.search_pois("小学", center, 1300, 20, 5)
    # "中关村中学" 噪音条目由服务层 filter_pattern 剔除，Provider 层原样返回 4 条
    assert len(education) == 4


async def test_replay_walking_route_matches_matrix_model() -> None:
    """降级链中间级回放（docs/02 §3.4）：walking_route 与 route_matrix 同模型——
    同一 OD 两法结果必须一致，否则降级前后判定口径漂移。"""
    provider = load_provider()
    origin = (116.316628, 39.981909)
    dests = [(116.326628, 39.981909), (116.316628, 39.991909)]
    matrix_legs = await provider.route_matrix(origin, dests)
    for dest, matrix_leg in zip(dests, matrix_legs, strict=True):
        walking_leg = await provider.walking_route(origin, dest)
        assert walking_leg.distance_m == matrix_leg.distance_m
        assert walking_leg.duration_s == matrix_leg.duration_s


def test_api_demo_flow() -> None:
    """API 级冒烟：health → demo hint → geocode → pois → 参数校验 422。"""
    with make_demo_app() as client:
        health = client.get("/api/v1/health").json()
        assert health["status"] == "ok"
        assert health["provider"] == "replay"

        hint = client.get("/api/v1/demo/hint").json()
        assert hint["origin"]["crs"] == "bd09"

        geo = client.get("/api/v1/geocode", params={"q": hint["address"]}).json()
        assert geo["candidates"], "示例地址应解析出坐标"

        origin = hint["origin"]
        pois = client.get(
            "/api/v1/pois",
            params={"lng": origin["lng"], "lat": origin["lat"], "categories": "medical,education"},
        ).json()
        assert set(pois["categories"]) == {"medical", "education"}
        names = [p["name"] for p in pois["categories"]["medical"]]
        assert "金象大药房(中关村店)" in names
        # 同名 30m 内去重：同名只出现一次
        assert names.count("金象大药房(中关村店)") == 1
        # 类目噪音被过滤：中学不出现在教育类目
        assert all("中学" not in n for n in [p["name"] for p in pois["categories"]["education"]])

        bad = client.get("/api/v1/pois", params={"lng": 10, "lat": 10, "categories": "foo"})
        assert bad.status_code == 422
        bad_coord = client.get("/api/v1/pois", params={"lng": 999, "lat": 10})
        assert bad_coord.status_code == 422
