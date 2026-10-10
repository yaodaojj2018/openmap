"""真实模式样本采集：跑完整社区分析并把报告固化到 data/samples/。

用途（docs/01 §8 交付清单）：docs/07 真实社区对比测试报告的素材 +
"示例数据（选定社区固化数据集）"。仅在真实模式（配置 OPENMAP_BAIDU_AK 且非
demo）下运行；每个社区一次完整分析，出站次数受 QuotaBudget 硬上限约束
（analysis_api_budget，默认 40）。

用法（WSL，backend/ 目录）：
    python3 scripts/collect_samples.py                     # 内置两社区（交道口/回龙观）
    python3 scripts/collect_samples.py --slug my --address "北京市..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.cache import MemoryCache
from app.core.config import REPO_ROOT, Settings
from app.core.coords import local_delta_m
from app.mapapi.baidu.client import BaiduClient
from app.models.common import Coord
from app.models.report import AnalysisReport
from app.models.task import AnalysisParams, TaskStatus
from app.tasks.manager import TaskManager

# 内置对比社区（docs/07）：老城胡同区 vs 大型居住区
COMMUNITIES: dict[str, str] = {
    "jiaodaokou": "北京市东城区交道口街道",
    "huilongguan": "北京市昌平区回龙观街道",
}


def _git_commit() -> str:
    """采集所基于的代码版本（报告可复现性）。git 不可用时返回 unknown。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(REPO_ROOT),
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def _level_metrics(
    origin_lng: float, origin_lat: float, ring: list[list[float]]
) -> dict[str, float]:
    """单级等时圈的形状量化（米制局部平面）：供对比报告描述各向异性。

    radius_cv（半径变异系数）大 = 边界凹凸剧烈（如老城被主干道/胡同切割）；
    aspect_ratio 偏离 1 = 圈形沿某轴拉伸（如被河流/铁路半包围）。
    """
    pts = [local_delta_m(origin_lng, origin_lat, lng, lat) for lng, lat in ring]
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    rs = [math.hypot(x, y) for x, y in pts]
    mean_r = sum(rs) / len(rs)
    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / len(rs))
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    return {
        "bbox_w_m": round(w, 1),
        "bbox_h_m": round(h, 1),
        "aspect_ratio": round(w / h, 3) if h > 0 else None,
        "max_radius_m": round(max(rs), 1),
        "min_radius_m": round(min(rs), 1),
        "radius_cv": round(std_r / mean_r, 3),
    }


def _derive_metrics(report: AnalysisReport) -> dict[str, object]:
    """报告派生指标：报告自带 area_km2，这里补形状量化与圈级面积比。"""
    lng, lat = report.origin.to_bd09()
    levels = []
    for lv in report.isochrone.levels:
        ring = lv.coordinates[0][0] if lv.coordinates and lv.coordinates[0] else []
        entry: dict[str, object] = {"level_min": lv.level_min, "area_km2": lv.area_km2}
        if ring:
            entry.update(_level_metrics(lng, lat, ring))
        levels.append(entry)
    areas = {lv.level_min: lv.area_km2 for lv in report.isochrone.levels}
    ratio = areas.get(15) / areas.get(5) if areas.get(5) else None
    return {
        "levels": levels,
        "area_ratio_15_to_5": round(ratio, 2) if ratio else None,
    }


async def collect(
    slug: str, address: str, settings: Settings
) -> tuple[dict[str, object] | None, int]:
    """采集单个社区：geocode → 完整分析 → 返回待固化载荷。落盘由同步侧执行。"""
    provider = BaiduClient(settings)
    mgr = TaskManager(provider, settings, MemoryCache())
    try:
        candidates = await provider.geocode(address)
        if not candidates:
            print(f"[{slug}] geocode 无结果：{address}")
            return None, 1
        geo = candidates[0]
        print(f"[{slug}] geocode → {geo.address} ({geo.lng:.6f},{geo.lat:.6f}) level={geo.level}")

        task = await mgr.create(AnalysisParams(origin=Coord(lng=geo.lng, lat=geo.lat)))
        final = await mgr.join(task.task_id)
        if final is None or final.status is not TaskStatus.completed:
            print(
                f"[{slug}] 分析未完成：status={final.status if final else '?'} "
                f"error={final.error if final else '?'}"
            )
            return None, 1
        report = await mgr.result(task.task_id)
        if report is None:
            print(f"[{slug}] completed 但报告缺失（不应发生）")
            return None, 1
    finally:
        await provider.close()

    if final.degraded_flags:
        print(f"[{slug}] ⚠ 降级标记（写入样本，供报告如实记录）：{final.degraded_flags}")

    payload = {
        "meta": {
            "slug": slug,
            "address": address,
            "resolved_address": geo.address,
            "resolved_level": geo.level,
            "origin": {"lng": geo.lng, "lat": geo.lat, "crs": "bd09"},
            "collected_at": datetime.now(UTC).isoformat(),
            "git_commit": _git_commit(),
            "settings": {
                "api_qps": settings.api_qps,
                "analysis_api_budget": settings.analysis_api_budget,
                "matrix_batch_size": settings.matrix_batch_size,
                "poi_search_radius_m": settings.poi_search_radius_m,
                "isochrone_directions": settings.isochrone_directions,
            },
        },
        "report": report.model_dump(mode="json"),
        "derived": _derive_metrics(report),
    }

    stats = final.api_call_stats
    areas = " ".join(f"{lv.level_min}min={lv.area_km2:.2f}km²" for lv in report.isochrone.levels)
    verdict = "PASS" if stats.get("http_calls", 99) <= 25 else "FAIL"
    print(f"[{slug}] http_calls={stats.get('http_calls')} (≤25 验收: {verdict})")
    print(f"[{slug}] stats={stats}")
    print(
        f"[{slug}] isochrone: method={report.isochrone.method} "
        f"probes={report.isochrone.probe_count} batches={report.isochrone.matrix_batches} {areas}"
    )
    print(f"[{slug}] overall_score={report.overall_score}")
    return payload, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", help="自定义社区标识（与 --address 成对出现）")
    parser.add_argument("--address", help="自定义社区地址（与 --slug 成对出现）")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "data" / "samples",
        help="输出目录（默认 data/samples/）",
    )
    parser.add_argument(
        "--gap",
        type=int,
        default=20,
        help="多社区采集间隔秒数（AK 并发窗口冷却，默认 20）",
    )
    args = parser.parse_args()

    targets: dict[str, str] = dict(COMMUNITIES)
    if args.slug or args.address:
        if not (args.slug and args.address):
            parser.error("--slug 与 --address 必须成对使用")
        targets = {args.slug: args.address}

    settings = Settings()
    if settings.demo_mode or not settings.baidu_ak:
        print("需要真实模式：配置 OPENMAP_BAIDU_AK 且 OPENMAP_DEMO_MODE=0")
        return 1

    codes = []
    for idx, (slug, address) in enumerate(targets.items()):
        if idx:
            # 百度并发配额按 AK 级滚动窗口计：背靠背第二个社区会叠加前一社区的
            # 余波触发连环 401 → 熔断打开误伤 POI 检索。等窗口清空再采。
            print(f"等待 {args.gap}s（AK 并发窗口冷却）...")
            time.sleep(args.gap)
        payload, code = asyncio.run(collect(slug, address, settings))
        if payload is not None:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            out_file = args.out_dir / f"{slug}.json"
            out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{slug}] → {out_file.relative_to(REPO_ROOT)}")
        codes.append(code)
    return max(codes)


if __name__ == "__main__":
    raise SystemExit(main())
