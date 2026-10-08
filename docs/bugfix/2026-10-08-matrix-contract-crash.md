# BF-006 覆盖精判矩阵响应缺行击穿降级守卫，整个分析任务报废

- 日期：2026-10-08 　提交：9cf77ba 　影响模块：mapapi/baidu/client.py、tasks/pipeline.py、coverage/funnel.py

## 现象

真实百度 `routematrix/v2/walking` 响应的结果行数少于目的地数（不可步行/坐标非法时
上游省略该行）时，分析任务在等时圈 + POI 的全部 API 预算花完之后以
`分析任务执行失败` 整体失败；回放/演示模式与 CI 永远无法复现。
代码注释声称"精判失败不拖垮报告"（docs/02 §3.4 降级链），实际未兑现。

## 根因分析

三层叠加，逐层定位：

1. **适配器不守约**：`BaiduClient.route_matrix` 做 `results = data.get("result") or []`
   后逐行 append，不校验行数与 `destinations` 一一对应——违反 `provider.py` 协议
   "返回顺序与 destinations 一一对应"的显式契约。
2. **域层防御被击穿**：`merge_matrix_verdicts` 用 `zip(edge_indices, legs, strict=True)`
   显式暴露契约违反（ValueError），这是正确的防御性设计；
   但 pipeline 覆盖阶段的降级守卫只写 `except MapApiError`——ValueError 直达
   TaskManager 的兜底 `except Exception` → `_fail()`。
3. **为什么 CI 测不到**：`ReplayProvider.route_matrix` 按目的地逐个生成 leg
   （不可达也返回 None-leg），恒满足 1:1——回放模式对该缺陷结构性免疫。

## 修复方案

- `mapapi/baidu/client.py`：route_matrix 每批校验 `len(results) == len(batch)`，
  不符即抛 `MapApiError(SERVER)`。缺失行无法对应回具体目的地，静默截断或补 None
  都会把耗时错配到别的设施，所以按上游故障处理（铁律 #3：故障策略收口在适配器，
  语义决策在编排层）。该修复同时保护等时圈引擎的 `field.ingest`（同为 strict zip）。
- `tasks/pipeline.py`：覆盖阶段守卫扩为 `except (MapApiError, ValueError)`——
  本阶段的承诺是"精判可失败"，任何失败面（含未来 Provider 违约）都不得击穿整体；
  非 MapApiError 的违约日志记 `kind=CONTRACT_VIOLATION`。

## 验证结果

```
tests/integration/test_baidu_client.py::test_route_matrix_truncated_rows_raise PASSED
tests/unit/test_task_manager.py::test_matrix_row_loss_degrades_coverage_not_task PASSED
tests/unit/test_coverage_funnel.py::test_merge_matrix_row_count_mismatch_raises PASSED
============================== 6 passed in 2.52s ===============================   （定向回归集）

$ python3 -m pytest -q --cov=app.isochrone --cov=app.poi --cov=app.coverage --cov=app.blindspot --cov-fail-under=95
Required test coverage of 95% reached. Total coverage: 97.93%
99 passed, 2 warnings in 12.06s
```

`test_matrix_row_loss_degrades_coverage_not_task` 通过注入正东 1050m 边缘带设施
（demo profile 0° detour=1.05/speed=1.35 → t̂≈817s ∈ [780,1020]s 边缘带）确定性触发
第 5 次矩阵调用并截断其响应，断言任务 completed + `coverage:matrix:unavailable` 降级标记。

## 影响范围与注意事项

- 波及：所有真实百度环境的覆盖精判调用；等时圈链路对截断响应从 ValueError 裸失败
  变为 MapApiError（重试耗尽后转译文案），语义不变但可观测性更好。
- 复发信号：日志出现 `analysis.coverage_matrix_degraded` 且 `kind=SERVER …行数…不符`，
  或 api_call_stats 中 route_matrix 计数与预算表（docs/02 §3.1.3）偏离。
- 回归测试节点：`test_route_matrix_truncated_rows_raise`（integration/test_baidu_client.py）、
  `test_matrix_row_loss_degrades_coverage_not_task`（unit/test_task_manager.py）、
  `test_merge_matrix_row_count_mismatch_raises`（unit/test_coverage_funnel.py）。
