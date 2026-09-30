"""射线时间场（纯函数域层，无 IO）。

以「方向 → {直线半径: 步行秒数 | None}」组织探针测量值。None 表示不可达
（河流/围墙/该方向无路网），并假设不可达单调：某半径不可达 ⇒ 更远皆不可达。
插值在直线距离域进行：同方向上步行时间近似随直线半径单调增（docs/02 §3.1）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from app.isochrone.sampler import Probe, direction_angles
from app.models.isochrone import RouteLeg


class RayStatus(StrEnum):
    """半径来源标注，驱动 confidence 计算与前端样式。"""

    OK = "ok"  # 级别落在实测区间内，线性插值
    EXTRAPOLATED = "extrapolated"  # 级别超过最远可达探针，按时间比例外推（封顶 1.3 倍）
    BLOCKED = "blocked"  # 方向存在不可达探针，取最远可达半径（真实阻挡，如河流）


@dataclass(frozen=True)
class RayRadius:
    """单方向在某级别的可达半径及其来源。"""

    theta: float
    radius_m: float
    status: RayStatus


class TimeField:
    """时间场：ingest 探针 → 查询级别半径 / 产出二分区间。"""

    def __init__(self, directions: int) -> None:
        self._rays: dict[float, dict[float, float | None]] = {
            theta: {} for theta in direction_angles(directions)
        }
        self.probe_count = 0

    def ingest(self, probes: list[Probe], legs: list[RouteLeg]) -> None:
        """写入一批探针测量值；同位置重复写入以最新为准（重试场景）。"""
        for probe, leg in zip(probes, legs, strict=True):
            ray = self._rays.setdefault(probe.theta, {})
            ray[probe.radius_m] = leg.duration_s
            self.probe_count += 1

    def _reachable(self, theta: float) -> list[tuple[float, float]]:
        """该方向可达样本 (半径, 秒数)，按半径升序。"""
        return sorted(
            (radius, duration)
            for radius, duration in self._rays[theta].items()
            if duration is not None
        )

    def level_radius(self, level_s: float) -> list[RayRadius]:
        """每个方向在该级别（秒）的可达半径。

        分支：区间内插值 / 原点至首探针插值 / 超出最远点（阻挡取远端、否则外推）。
        """
        out: list[RayRadius] = []
        for theta in sorted(self._rays):
            ray = self._rays[theta]
            reachable = self._reachable(theta)
            if level_s <= 0 or not reachable:
                status = RayStatus.OK if level_s <= 0 else RayStatus.BLOCKED
                out.append(RayRadius(theta, 0.0, status))
                continue

            first_r, first_t = reachable[0]
            if level_s <= first_t:
                # 原点 (0,0) 与最近可达探针之间插值
                out.append(RayRadius(theta, first_r * level_s / first_t, RayStatus.OK))
                continue

            far_r, far_t = reachable[-1]
            if level_s <= far_t:
                out.append(self._interp_crossing(theta, reachable, level_s))
                continue

            blocked = [radius for radius, t in ray.items() if t is None]
            if blocked:
                # 阻挡方向：真实边界在最远可达点与最近不可达点之间，取保守端
                out.append(RayRadius(theta, far_r, RayStatus.BLOCKED))
            else:
                # 纯采样不足：按恒速比例外推，封顶 1.3 倍防发散
                extrapolated = min(far_r * level_s / far_t, far_r * 1.3)
                out.append(RayRadius(theta, extrapolated, RayStatus.EXTRAPOLATED))
        return out

    @staticmethod
    def _interp_crossing(
        theta: float, reachable: list[tuple[float, float]], level_s: float
    ) -> RayRadius:
        """找到时间穿越 level_s 的相邻样本对做线性插值；时间非单调时退回最远点。"""
        for (r1, t1), (r2, t2) in pairwise(reachable):
            if t1 <= level_s <= t2:
                ratio = (level_s - t1) / (t2 - t1) if t2 > t1 else 1.0
                return RayRadius(theta, r1 + ratio * (r2 - r1), RayStatus.OK)
        return RayRadius(theta, reachable[-1][0], RayStatus.OK)

    def refine_brackets(
        self, level_s: float, min_gap_m: float = 50.0
    ) -> list[tuple[float, float, float]]:
        """产出值得二分的区间 (theta, r_lo, r_hi)：时间穿越级别、宽度 > min_gap。

        上端点必为可达样本（原点视作 0 秒可达）；阻挡/外推方向不参与二分。
        """
        brackets: list[tuple[float, float, float]] = []
        for theta in sorted(self._rays):
            reachable = self._reachable(theta)
            if not reachable:
                continue
            with_origin: list[tuple[float, float]] = [(0.0, 0.0), *reachable[:-1]]
            for (r1, t1), (r2, t2) in zip(with_origin, reachable, strict=True):
                if t1 <= level_s <= t2 and (r2 - r1) > min_gap_m:
                    brackets.append((theta, r1, r2))
                    break
        return brackets
