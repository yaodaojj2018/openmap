# BF-008 阻挡方向判定口径失真：插值冒充实测 + 几何先验虚高可达率

- 日期：2026-10-08 　提交：1203f98 　影响模块：isochrone/field.py、coverage/funnel.py

## 现象

河流/围墙阻挡方向附近的设施，覆盖判定系统性偏乐观（两处独立失真叠加）：

1. 设施位于阻挡射线与开放射线之间（如 157.5° 河流、22.5° 后方），二级漏斗
   直接返回开放射线的时间并以 0.8 置信度判圈内——永不进三级矩阵精判，
   实际直行路线被阻挡的设施在报告里"可达"。
2. 估算返回 None（阻挡）的圈内设施以几何先验兜底 `in_circle=True`；精判配额
   （coverage_matrix_verify_limit=30）截断或矩阵降级（BF-006 路径）时不再实测，
   这些设施全部计入 reachable——类目可达率与评分虚高。

## 根因分析

- 失真一（`field.py estimate_seconds`）：`if t_lo is None: return t_hi`（t_hi 同理）
  ——单侧射线阻挡时全值采用另一侧，角权重 weight 算了却不用。docstring 自己声明
  "阻挡方向…返回 None——由调用方归入边缘带矩阵精判，不用插值口径冒充结论"，
  实现与契约不符。既有单测 `test_estimate_seconds_blocked_beyond_barrier` 只覆盖
  双侧皆 None 分支（相邻射线恰好为空），混合分支零覆盖——测试缺口掩盖了契约违背。
- 失真二（`funnel.py classify_facilities`）：`est_s is None and polygon is not None
  → model_copy(in_circle=inside)` 的"几何先验"补丁。圈内未知 ≠ 圈内可达：
  精判队列虽把 None 排首位，但配额/降级路径下未实测的先验 True 全部流入
  `summarize_categories` 的 reachable 计数。

## 修复方案

两处统一到"未知宁可少算不可多算，由实测纠偏"的保守口径：

- `field.py`：单侧 None 即返回 None（删除两个全值回退分支），与 docstring 契约对齐；
  未知方位交边缘带矩阵精判定论。生产场 16 射线全有采样，仅阻挡语义触发 None。
- `funnel.py`：删除几何先验补丁，估算不可用一律保守 `in_circle=False`
  （置信度减半 0.4 保留），仍优先排精判队列首位由实测翻转；docstring 同步。
- `models/coverage.py`：`est_walk_time_min` 的 None 语义描述补"阻挡方向未估算"。

## 验证结果

```
tests/unit/test_isochrone.py::test_estimate_seconds_one_blocked_ray_returns_none PASSED
tests/unit/test_coverage_funnel.py::test_unestimatable_point_conservative_and_verifies_first PASSED
============================== 6 passed in 2.52s ===============================   （定向回归集）

$ python3 -m pytest -q --cov=app.isochrone --cov=app.poi --cov=app.coverage --cov=app.blindspot --cov-fail-under=95
Required test coverage of 97.93% reached (gate 95%)
99 passed, 2 warnings in 12.06s
```

单测构造：东向射线 {300:300, 600:None}（河流@600m）+ 东北向开放，查询 22.5°/700m
点 → 修复前返回东北向全值（≈开放侧时间），修复后 None（进边缘带精判）。

## 影响范围与注意事项

- 波及：阻挡方向 ±1 射线扇区内、且在等时圈多边形内的设施判定（更保守、更准确）；
  边缘带精判队列可能变长（None 优先级最高，配额 30 内优先消耗）。
- 行为变化：既有单测 `test_estimate_seconds_on_ray_interpolates` 原依赖"相邻空射线
  回退本射线值"的宽松行为，已改为相邻射线补样本（生产场射线恒有样本，宽松回退
  本就不该作为契约）。
- 复发信号：阻挡方向设施的 method=FIELD 且 est 非 None；或精判降级时 reachable
  计数包含 est_walk_time_min=None 的设施。
- 回归测试节点：`test_estimate_seconds_one_blocked_ray_returns_none`
  （unit/test_isochrone.py）、`test_unestimatable_point_conservative_and_verifies_first`
  （unit/test_coverage_funnel.py）。
