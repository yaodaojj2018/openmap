"""多源并集等时圈端到端（回放模式）：出入口输入 → 并集等时圈 + 覆盖 + 盲区。

docs/02 §3.1 多源并集：entry_points 驱动等时圈（逐出入口各算再 unary_union），
origin 自动取质心用于 POI/盲区/报告；单点缺省 = 现有行为。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ORIGIN = {"lng": 116.316628, "lat": 39.981909}
ENTRY_POINTS = [
    {"lng": 116.315628, "lat": 39.981909},
    {"lng": 116.317628, "lat": 39.981909},
]


def _wait_terminal(client: TestClient, task_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/analyses/{task_id}/status").json()
        if body["status"] not in ("pending", "running"):
            return body
        time.sleep(0.05)
    pytest.fail(f"任务 {task_id} 超时未完成")


def test_multisource_analysis_flow() -> None:
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        # 仅给 entry_points：中心自动取质心
        created = client.post("/api/v1/analyses", json={"entry_points": ENTRY_POINTS})
        assert created.status_code == 202
        task_id = created.json()["task_id"]

        final = _wait_terminal(client, task_id)
        assert final["status"] == "completed"

        body = client.get(f"/api/v1/analyses/{task_id}/result").json()
        # 质心 = 两出入口均值
        assert body["origin"]["lng"] == pytest.approx(ORIGIN["lng"])
        assert body["origin"]["lat"] == pytest.approx(ORIGIN["lat"])
        # 报告与等时圈都回带出入口清单（前端重连据此重绘标记）
        assert len(body["entry_points"]) == 2
        assert len(body["isochrone"]["origins"]) == 2

        # 等时圈为 MultiPolygon 契约（coordinates 是多边形数组）
        for lv in body["isochrone"]["levels"]:
            assert isinstance(lv["coordinates"], list)
            assert isinstance(lv["coordinates"][0][0][0], list)
        assert [lv["level_min"] for lv in body["isochrone"]["levels"]] == [5, 10, 15]

        # 面积：并集几何回投米制，必须为正且随级别递增（回归钉：一度为 0.00 km²）
        areas = [lv["area_km2"] for lv in body["isochrone"]["levels"]]
        assert 0 < areas[0] < areas[1] < areas[2]

        # 覆盖与盲区仍随报告产出（盲区以质心为原点）
        assert body["coverage"]["threshold_min"] == 15
        assert {t["type_key"] for t in body["blindspot"]["types"]} == {
            "medical",
            "education",
            "shopping",
        }


def test_multisource_requires_origin_or_entry_points() -> None:
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        bad = client.post("/api/v1/analyses", json={})
        assert bad.status_code == 422
        too_many = client.post("/api/v1/analyses", json={"entry_points": ENTRY_POINTS * 2})
        assert too_many.status_code == 422


def test_single_origin_backward_compatible() -> None:
    """单点 origin 缺省 entry_points 时行为与多源实现前一致（回归钉）。"""
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        created = client.post("/api/v1/analyses", json={"origin": ORIGIN})
        assert created.status_code == 202
        body = _wait_terminal(client, created.json()["task_id"])
        assert body["status"] == "completed"

        result = client.get(f"/api/v1/analyses/{created.json()['task_id']}/result").json()
        assert result["isochrone"]["origins"][0]["lng"] == pytest.approx(ORIGIN["lng"])
        assert len(result["isochrone"]["origins"]) == 1
        # 单点走 _merge_iso 透传，面积同样必须为正（同 bug 影响面）
        areas = [lv["area_km2"] for lv in result["isochrone"]["levels"]]
        assert 0 < areas[0] < areas[1] < areas[2]
