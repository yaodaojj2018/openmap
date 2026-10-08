"""射线时间场（纯函数域层，无 IO）。

以「方向 → {直线半径: 步行秒数 | None}」组织探针测量值。None 表示不可达
（河流/围墙/该方向无路网），并假设不可达单调：某半径不可达 ⇒ 更远皆不可达。
插值在直线距离域进行：同方向上步行时间近似随直线半径单调增（docs/02 §3.1）。
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from app.core.coords import local_delta_m
from app.isochrone.sampler import Probe, direction_angles
from app.mapapi.provider import BD09Point
from app.models.isochrone import RouteLeg

_TAU = 2 * math.pi


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

    def scatter(
        self, extrapolate_to_m: float | None = None
    ) -> tuple[list[tuple[float, float]], list[float]]:
        """可达样本散点（米制坐标 + 秒数），供网格插值（docs/02 §3.1 阶段 C 第二法）。

        extrapolate_to_m 给定时，为「最远可达半径之外」的方向补一个该半径处的
        虚拟散点，取值沿用 _time_at 的封顶 1.3× 外推口径——否则快方向（级别圈
        落在最远探针之外）的等值线落在网格外无法闭合，第二法直接失效。
        阻挡方向不外推：那不是采样不足，而是真实不可达，虚构会抹平凹陷。
        """
        points: list[tuple[float, float]] = []
        times: list[float] = []
        for theta in sorted(self._rays):
            ray = self._rays[theta]
            for radius, duration in sorted(ray.items()):
                if duration is None:
                    continue
                points.append((radius * math.cos(theta), radius * math.sin(theta)))
                times.append(duration)
            reachable = self._reachable(theta)
            if extrapolate_to_m is None or not reachable:
                continue
            if any(duration is None for duration in ray.values()):
                continue
            far_r, _ = reachable[-1]
            if extrapolate_to_m > far_r:
                extrapolated = self._time_at(theta, extrapolate_to_m)
                if extrapolated is not None:
                    points.append(
                        (extrapolate_to_m * math.cos(theta), extrapolate_to_m * math.sin(theta))
                    )
                    times.append(extrapolated)
        return points, times

    def estimate_seconds(self, origin: BD09Point, lng: float, lat: float) -> float | None:
        """任意点的步行秒数估算（覆盖判定二级漏斗，docs/02 §3.2）。

        口径：定位到包围该点方位角的两条相邻射线，各自按半径线性插值/外推
        （封顶 1.3×，与 level_radius 同源），再做角度线性加权。
        阻挡方向（该半径之前已有不可达探针）或全程无样本返回 None——
        由调用方归入边缘带矩阵精判，不用插值口径冒充结论。
        """
        thetas = sorted(self._rays)
        if not thetas:
            return None
        dx_m, dy_m = local_delta_m(origin[0], origin[1], lng, lat)
        radius = math.hypot(dx_m, dy_m)
        if radius <= 0.0:
            return 0.0

        theta = math.atan2(dy_m, dx_m) % _TAU
        lo = thetas[bisect.bisect_right(thetas, theta) - 1]  # -1 回绕到最后一条射线
        hi = thetas[0] if lo == thetas[-1] else thetas[bisect.bisect_right(thetas, theta)]
        span = (hi - lo) % _TAU or _TAU
        weight = ((theta - lo) % _TAU) / span

        t_lo = self._time_at(lo, radius)
        t_hi = self._time_at(hi, radius)
        if t_lo is None or t_hi is None:
            # 任一相邻射线在该半径阻挡/无样本：该方位的插值失去依据，
            # 取另一侧全值属于用插值口径冒充结论——返回 None 交边缘带矩阵精判
            return None
        return t_lo + (t_hi - t_lo) * weight

    def _time_at(self, theta: float, radius: float) -> float | None:
        """单射线上给定半径的秒数：区间插值 / 原点插值 / 外推（封顶 1.3×）。"""
        ray = self._rays[theta]
        if any(r <= radius for r, t in ray.items() if t is None):
            return None  # 半径之内存在不可达探针：阻挡，不做插值冒充
        reachable = self._reachable(theta)
        if not reachable:
            return None
        first_r, first_t = reachable[0]
        if radius <= first_r:
            return first_t * radius / first_r
        for (r1, t1), (r2, t2) in pairwise(reachable):
            if r1 <= radius <= r2:
                ratio = (radius - r1) / (r2 - r1) if r2 > r1 else 1.0
                return t1 + ratio * (t2 - t1)
        far_r, far_t = reachable[-1]
        return min(far_t * radius / far_r, far_t * 1.3)

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
