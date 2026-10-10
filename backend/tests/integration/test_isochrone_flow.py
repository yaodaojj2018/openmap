"""等时圈端到端测试（回放模式）：M2 验收——15min 圈成形、几何有效、预算受控。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import REPO_ROOT, Settings
from app.core.coords import haversine_m
from app.isochrone.engine import IsochroneError, compute_isochrone
from app.isochrone.grid import build_grid, contour_polygons
from app.isochrone.reconcile import grid_consistency
from app.main import create_app
from app.mapapi.replay.client import ReplayProvider

ORIGIN = (116.316628, 39.981909)


def load_provider() -> ReplayProvider:
    return ReplayProvider.from_file(str(REPO_ROOT / "data" / "replays" / "demo.json"))


async def test_engine_levels_geometry_and_budget() -> None:
    computation = await compute_isochrone(
        load_provider(), Settings(demo_mode=True), ORIGIN, [5, 10, 15]
    )
    result = computation.result

    assert [lv.level_min for lv in result.levels] == [5, 10, 15]
    areas = [lv.area_km2 for lv in result.levels]
    assert 0 < areas[0] < areas[1] < areas[2], "三级面积必须严格递增"
    assert areas[2] > 2.0, "回放快照下 15min 圈应为 km² 量级"

    # 预算口径（docs/02 §3.1.3）：粗采样 2 批 + 二分 ≤ 3 批 + 双法加密 ≤ 1 批，
    # 远低于单次分析 40 次上限
    assert result.matrix_batches <= 7
    assert 64 < result.probe_count <= 64 + 3 * 16 + Settings().isochrone_densify_max_probes

    for lv in result.levels:
        ring = lv.coordinates[0][0]
        assert ring[0] == ring[-1], "GeoJSON 环必须闭合"
        assert len(ring) >= 160, "16 方向 × 每段 10 平滑采样点"

    # 置信度：5min 全方向实测且双法一致 = 1.0；15min 存在外推/阻挡方向应 < 1
    assert result.levels[0].confidence == 1.0
    assert 0.8 <= result.levels[2].confidence < 1.0


async def test_dual_method_geometry_agreement_on_snapshot() -> None:
    """双法等时圈几何稳定性（容差比较）：网格等值线与射线样条各自独立算出同级圈。

    锁的是 M4 阶段 C 的几何口径——两条互不依赖的路径（极坐标样条 vs
    griddata+marching squares）面积差必须在 10% 量级内，且逐方向临界半径偏差
    不超容差（一致性 ≥ 0.8）。任一方的实现被改坏（如查表笔误把等值点甩出网格）
    都会在此暴露，而不必依赖外部 API。
    """
    computation = await compute_isochrone(
        load_provider(), Settings(demo_mode=True), ORIGIN, [5, 10, 15]
    )
    settings = Settings(demo_mode=True)
    extent_m = float(settings.isochrone_grid_extent_m)
    points, times = computation.field.scatter(extrapolate_to_m=extent_m)
    grid = build_grid(points, times, float(settings.isochrone_grid_step_m), extent_m)
    assert grid is not None, "回放快照样本量足够，第二法必须可用"

    for lv in computation.result.levels:
        level_s = lv.level_min * 60
        polygons = contour_polygons(grid, level_s)
        assert polygons, f"{lv.level_min}min 等值线必须闭合"
        contour_area = polygons[0].area / 1e6
        assert contour_area == pytest.approx(lv.area_km2, rel=0.10), (
            f"{lv.level_min}min 双法面积背离：等值线 {contour_area} vs 样条 {lv.area_km2}"
        )
        consistency = grid_consistency(
            computation.field.level_radius(level_s),
            polygons,
            settings.isochrone_verify_tolerance_m,
            extent_m,
        )
        assert consistency is not None and consistency >= 0.8


async def test_engine_densifies_when_methods_disagree() -> None:
    """阶段 D 接线：容差收紧到"处处判背离"时，加密探针合成 1 次批量矩阵调用。

    用把容差压到 0.001m 的方式让每个非阻挡方向都触发加密（真实现场由双法偏差
    触发），验证的是编排而非判定——判定本身由 reconcile 单测覆盖。加密失败
    属可选精度层，不得把整份等时圈标成降级。
    """
    provider = load_provider()
    base = (await compute_isochrone(provider, Settings(demo_mode=True), ORIGIN, [15])).result
    tight = (
        await compute_isochrone(
            load_provider(),
            Settings(demo_mode=True, isochrone_verify_tolerance_m=0.001),
            ORIGIN,
            [15],
        )
    ).result

    limit = Settings().isochrone_densify_max_probes
    assert tight.probe_count == base.probe_count + limit
    assert tight.matrix_batches == base.matrix_batches + 1
    assert tight.degraded_reason is None, "加密是精度层，不改变实测口径的降级声明"
    assert tight.method == base.method


async def test_engine_blocked_direction_dent() -> None:
    """回放快照 157.5° 方向 cap=450（模拟河流）：15min 圈在该方向有明显凹陷。"""
    result = (
        await compute_isochrone(load_provider(), Settings(demo_mode=True), ORIGIN, [15])
    ).result
    ring = result.levels[0].coordinates[0][0]
    dists = [haversine_m(ORIGIN[0], ORIGIN[1], lng, lat) for lng, lat in ring]
    # 凹陷方向边界贴近 300m 保守可达点；开阔方向超过 1000m
    assert min(dists) < 450.0
    assert max(dists) > 1000.0


async def test_engine_rejects_out_of_range_levels() -> None:
    with pytest.raises(IsochroneError):
        await compute_isochrone(load_provider(), Settings(demo_mode=True), ORIGIN, [45])


@pytest.mark.smoke
def test_api_isochrone_flow() -> None:
    """API 级：默认级别 / 自定义级别 / crs 转换 / 422 校验。"""
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        ok = client.post("/api/v1/isochrone", json={"origin": {"lng": ORIGIN[0], "lat": ORIGIN[1]}})
        assert ok.status_code == 200
        body = ok.json()
        assert body["origin"]["crs"] == "bd09"
        assert body["method"] == "ray-spline-v1"
        assert [lv["level_min"] for lv in body["levels"]] == [5, 10, 15]

        custom = client.post(
            "/api/v1/isochrone",
            json={
                "origin": {"lng": ORIGIN[0], "lat": ORIGIN[1], "crs": "wgs84"},
                "levels_min": [10],
            },
        )
        assert custom.status_code == 200
        assert [lv["level_min"] for lv in custom.json()["levels"]] == [10]
        # wgs84 输入已转到 bd09：中心点应与 bd09 原点不同但接近（百米级偏移）
        assert abs(custom.json()["origin"]["lng"] - ORIGIN[0]) > 1e-4

        bad_levels = client.post(
            "/api/v1/isochrone",
            json={"origin": {"lng": ORIGIN[0], "lat": ORIGIN[1]}, "levels_min": [45]},
        )
        assert bad_levels.status_code == 422
        assert bad_levels.json()["detail"]["code"] == "BAD_LEVELS"

        bad_coord = client.post("/api/v1/isochrone", json={"origin": {"lng": 200, "lat": 39.9}})
        assert bad_coord.status_code == 422
