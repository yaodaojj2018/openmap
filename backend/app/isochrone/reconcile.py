"""双法交叉校验与加密选点（纯函数域层，无 IO）。

docs/02 §3.1 阶段 C/D：射线样条（第一法，主法，保证闭合平滑）与网格等值线
（第二法，校验）互为独立口径。本模块只做判定与选点，不做测量——加密探针由
调用方（engine）合并成一次批量矩阵调用回收，测完重拟合。

三件事：
1. `grid_consistency` —— 逐方向比对两法临界半径，给出 0-1 一致性，作为
   fitter 置信度的折算依据（两法背离 = 拟合质量证据不足）；
2. `densify_probes` —— 偏差超阈值的方位，在等值线主张的半径上补采；
3. `enclave_probes` —— 相邻方向临界半径突增的扇区，扇区中点补采判飞地。

**阻挡方向（RayStatus.BLOCKED）一律不参与本模块**：第二法由"可达样本"构建，
结构上无法表达"这里过不去"（散点里没有 None），两法在阻挡处的分歧是口径差异
而非拟合质量证据——与 fitter._confidence 把阻挡计为确定信息的判定同源。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from app.isochrone.field import RayRadius, RayStatus
from app.isochrone.grid import radial_distance
from app.isochrone.sampler import Probe, offset_point
from app.mapapi.provider import BD09Point

_TAU = 2 * math.pi


def grid_consistency(
    radii: Sequence[RayRadius],
    contours: Sequence[Polygon],
    tolerance_m: float,
    max_r_m: float,
) -> float | None:
    """第二法一致性 0-1：|样条临界半径 − 等值线临界半径| ≤ tolerance 的方向占比。

    任一参与方向在等值线上取不到边界（该级别圈未闭合到网格内）即整体返回
    None——第二法对本级别不可用，调用方保留单法置信度，绝不用"默认一致"冒充
    校验通过。全部方向皆阻挡时同样返回 None（无可比对量）。
    """
    geom = _primary(contours)
    if geom is None:
        return None
    compared = 0
    agree = 0
    for rr in radii:
        if rr.status is RayStatus.BLOCKED:
            continue
        other = radial_distance(geom, rr.theta, max_r_m)
        if other is None:
            return None  # 第二法在该方位无边界：整体判不可用
        compared += 1
        if abs(other - rr.radius_m) <= tolerance_m:
            agree += 1
    if compared == 0:
        return None
    return round(agree / compared, 3)


def densify_probes(
    radii: Sequence[RayRadius],
    contours: Sequence[Polygon],
    origin: BD09Point,
    tolerance_m: float,
    max_r_m: float,
    limit: int,
) -> list[Probe]:
    """双法偏差 > tolerance 的方位：在等值线主张的半径上补 1 探针。

    选点理由：偏差段内两法各执一词，等值线是"该半径恰好等于级别耗时"这一
    更强主张——直接实测它是否成立，一次测量即可判定谁可信（实测秒数远小于
    级别 ⇒ 样条偏保守；接近级别 ⇒ 等值线成立，样条待外扩）。
    按 theta 升序取前 limit 个，保证同一时间场的多次调用选点可复现。
    """
    geom = _primary(contours)
    if geom is None or limit <= 0:
        return []
    out: list[Probe] = []
    for rr in sorted(radii, key=lambda item: item.theta):
        if len(out) >= limit:
            break
        if rr.status is RayStatus.BLOCKED:
            continue
        other = radial_distance(geom, rr.theta, max_r_m)
        if other is None or other <= 0 or abs(other - rr.radius_m) <= tolerance_m:
            continue
        radius = min(other, max_r_m)
        lng, lat = offset_point(origin, rr.theta, radius)
        out.append(Probe(theta=rr.theta, radius_m=radius, lng=lng, lat=lat))
    return out


def enclave_probes(
    radii: Sequence[RayRadius],
    origin: BD09Point,
    jump_ratio: float,
    max_r_m: float,
    limit: int,
) -> list[Probe]:
    """相邻方向临界半径比 ≥ jump_ratio → 疑似飞地扇区，扇区中点补 2 探针。

    射线模型只看得到边界半径的突变、看不到"圈内空洞"：相邻两方向对可达性的
    判断出现数量级分歧时，扇区中点的真实可达性才是判据。故在中点方位上、
    按两侧各自主张的半径各测一次——若两者都不可达则是真飞地（口袋），
    若可达则说明突变源于采样噪声，样条可放心平滑过去。

    口径说明：判定用"相邻半径比 ≥ jump_ratio"（默认 2×，即突变倍数达量级）
    而非与全局中位数比较——中位数在方向数少时被单个异常值拖偏，比较相邻对
    更贴合"突变"本义。零半径方向是确定阻挡（边界贴原点），不参与。
    """
    ordered = sorted(radii, key=lambda item: item.theta)
    if len(ordered) < 2 or limit < 1 or jump_ratio <= 1.0:
        return []
    out: list[Probe] = []
    seen: set[tuple[float, float]] = set()
    for prev, cur in pairwise([*ordered, ordered[0]]):
        if len(out) >= limit:
            break
        if prev.status is RayStatus.BLOCKED or cur.status is RayStatus.BLOCKED:
            continue
        r_lo, r_hi = sorted((prev.radius_m, cur.radius_m))
        if r_lo <= 0 or r_hi < jump_ratio * r_lo:
            continue
        theta_mid = _mid_angle(prev.theta, cur.theta)
        for radius in (min(r_lo, max_r_m), min(r_hi, max_r_m)):
            if len(out) >= limit:
                break
            if (theta_mid, radius) in seen:
                continue
            seen.add((theta_mid, radius))
            lng, lat = offset_point(origin, theta_mid, radius)
            out.append(Probe(theta=theta_mid, radius_m=radius, lng=lng, lat=lat))
    return out


def _primary(contours: Sequence[Polygon]) -> BaseGeometry | None:
    """等值线面集合 → 主区域（面积最大者）；空集合返回 None。

    只取主区域而非 unary_union：单源步行的可达集是连通的，主区域就是级别圈的
    外边界；其余碎片的来源有二——稀疏采样区（阻挡扇区的三角剖分）插值出的假
    结构，或真正的"可达孤岛"。前者会把 radial_distance 的取最大值抬到网格边缘
    造成假偏差，后者属于飞地议题而非外边界比对，故都不参与双法比对。
    按面积取最大而非取首元素：调用方可能传未排序的集合。
    """
    parts = [poly for poly in contours if not poly.is_empty]
    return max(parts, key=lambda poly: poly.area) if parts else None


def _mid_angle(theta_a: float, theta_b: float) -> float:
    """两方位角的角平分线（取短弧一侧，结果归一化到 [0, 2π)）。"""
    if abs(theta_b - theta_a) > math.pi:  # 跨越 0 方位角
        return ((theta_a + theta_b + _TAU) / 2) % _TAU
    return (theta_a + theta_b) / 2
