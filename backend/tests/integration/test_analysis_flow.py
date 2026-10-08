"""分析任务 API 端到端（回放模式）：202 创建 → 轮询兜底 → 结果获取 → SSE 事件流。

docs/02 §5.3 契约的行为级验证；主链路为 smoke 冒烟门禁的一部分。
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.core.cache import MemoryCache
from app.core.config import REPO_ROOT, Settings
from app.main import create_app
from app.mapapi.provider import BD09Point, MapProvider
from app.mapapi.replay.client import ReplayProvider
from app.models.geocode import GeocodeCandidate
from app.models.isochrone import RouteLeg
from app.models.poi import PoiRecord
from app.tasks.manager import TaskManager

ORIGIN = {"lng": 116.316628, "lat": 39.981909}


class _SlowMatrixProvider:
    """仅矩阵调用加延迟——构造确定性的"运行中"任务（其余能力直接透传回放）。"""

    def __init__(self, inner: MapProvider, delay_s: float) -> None:
        self._inner = inner
        self._delay_s = delay_s
        self.name = inner.name

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodeCandidate]:
        return await self._inner.geocode(address, city)

    async def search_pois(
        self,
        query: str,
        center: BD09Point,
        radius_m: int,
        page_size: int,
        max_pages: int,
    ) -> list[PoiRecord]:
        return await self._inner.search_pois(query, center, radius_m, page_size, max_pages)

    async def route_matrix(
        self, origin: BD09Point, destinations: list[BD09Point]
    ) -> list[RouteLeg]:
        await asyncio.sleep(self._delay_s)
        return await self._inner.route_matrix(origin, destinations)

    async def close(self) -> None:
        await self._inner.close()


def _wait_terminal(client: TestClient, task_id: str, timeout_s: float = 10.0) -> dict:
    """轮询兜底通道（docs/02 §5.3 /status）：等任务离开 pending/running。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/analyses/{task_id}/status").json()
        if body["status"] not in ("pending", "running"):
            return body
        time.sleep(0.05)
    pytest.fail(f"任务 {task_id} 超时未完成")


@pytest.mark.smoke
def test_analysis_api_flow() -> None:
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        created = client.post("/api/v1/analyses", json={"origin": ORIGIN})
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        assert created.json()["status"] in ("pending", "running")

        final = _wait_terminal(client, task_id)
        assert final["status"] == "completed"
        assert final["progress"] == 1.0
        assert final["api_call_stats"]["route_matrix"] >= 4

        result = client.get(f"/api/v1/analyses/{task_id}/result")
        assert result.status_code == 200
        body = result.json()
        assert [lv["level_min"] for lv in body["isochrone"]["levels"]] == [5, 10, 15]
        assert set(body["poi"]["categories"]) == {"medical", "education", "shopping", "elderly"}
        assert body["origin"]["crs"] == "bd09"
        # M3：覆盖判定 + 盲区识别随报告产出
        assert body["coverage"]["threshold_min"] == 15
        assert len(body["coverage"]["categories"]) == 4
        assert {t["type_key"] for t in body["blindspot"]["types"]} == {
            "medical",
            "education",
            "shopping",
        }
        # 综合评分 = 类目评分等权平均（docs/02 §5.4 HealthReport.overall_score）
        scores = [c["score"] for c in body["coverage"]["categories"]]
        assert body["overall_score"] == round(sum(scores) / len(scores), 1)

        # 未知任务 / 非法参数
        assert client.get("/api/v1/analyses/unknown/status").status_code == 404
        bad = client.post("/api/v1/analyses", json={"origin": ORIGIN, "minutes": 45})
        assert bad.status_code == 422


@pytest.mark.smoke
def test_analysis_sse_stream() -> None:
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        # 换中心点避免命中上一个测试的参数复用
        origin2 = {"lng": ORIGIN["lng"] + 0.002, "lat": ORIGIN["lat"]}
        created = client.post("/api/v1/analyses", json={"origin": origin2})
        task_id = created.json()["task_id"]

        collected: list[tuple[str, str]] = []
        with client.stream("GET", f"/api/v1/analyses/{task_id}/events") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            event_name = ""
            data_parts: list[str] = []
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_parts.append(line.split(":", 1)[1].strip())
                elif line == "" and event_name:
                    collected.append((event_name, "".join(data_parts)))
                    if event_name in ("completed", "failed", "cancelled"):
                        break
                    event_name, data_parts = "", []

        names = [name for name, _ in collected]
        assert "stage" in names and "progress" in names
        assert names[-1] == "completed", "SSE 流应以 completed 终止"

        completed_payload = collected[-1][1]
        assert '"status":"completed"' in completed_payload.replace(" ", "")


def test_analysis_result_conflict_while_running() -> None:
    """未完成任务取结果 → 409（docs/02 §5.3 错误码规范）。"""
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        # 换慢 Provider 拉长任务，确保 GET /result 时仍在运行
        app.state.task_manager = TaskManager(
            _SlowMatrixProvider(
                ReplayProvider.from_file(str(REPO_ROOT / "data" / "replays" / "demo.json")), 0.2
            ),
            app.state.settings,
            MemoryCache(),
        )
        created = client.post("/api/v1/analyses", json={"origin": ORIGIN})
        task_id = created.json()["task_id"]

        resp = client.get(f"/api/v1/analyses/{task_id}/result")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "TASK_NOT_COMPLETED"
