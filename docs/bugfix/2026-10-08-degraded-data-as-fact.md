# BF-007 检索失败/未选类目被当成事实结论：盲区假警报 + 综合评分虚低

- 日期：2026-10-08 　提交：1203f98 　影响模块：blindspot/grid.py、tasks/pipeline.py、models/report.py

## 现象

两个用户可见的错误结论，同根因（"没有数据"被下游当成"数据说没有"）：

1. 用户只勾选部分类目（如仅 medical+elderly），或某类目 POI 检索瞬时失败
   （降级标记 `poi:shopping:unavailable`、`facilities["shopping"]=[]`）时，
   盲区报告给出"缺购物 225 格 · 全域缺失"、整幅 1.5km 地图灰色叠加、
   "复合盲区 225 格，建议优先补足"——把数据缺失呈现为盲区事实。
2. 综合评分把该失败类目的硬 0 分计入等权平均（4 类默认 → 单类目故障拉低约 25 分），
   仪表盘把一次数据故障呈现为社区质量差。

## 根因分析

- 盲区：`compute_blindspot` 固定遍历 `settings.blindspot_types`（配置常量，与用户
  选择/检索成败无关），`facilities.get(key, [])` 拿到空列表就走 `missing = np.ones(全格)`。
  "空设施列表"在栅格语义里是**真实无设施 → 全域缺失**（这是正确语义），但编排层
  把"未搜索/搜索失败"的空列表也喂了进去——两种空不可区分，域层无从判断。
- 评分：`overall_score = mean(所有 params.categories 的 score)`，而失败类目经
  `summarize_categories` 得 0 分（空类目显式 0 分本是对"真实无设施"的正确口径，
  docs/02 §3.4），失败与真空无法区分导致数据故障混入评分。
- 定位路径：对 `test_poi_single_category_failure_degrades` 补盲区/评分断言复现。

## 修复方案

编排层显式区分两种空（域层语义不变）：

- `tasks/pipeline.py` POI 阶段收集 `failed_keys`（MapApiError 的类目）；
  盲区阶段只把 `blindspot_types ∩ (params.categories - failed_keys)` 注入
  `compute_blindspot`（新参数 `types`，缺省回退 settings.blindspot_types 保持域层
  可独立单测）；综合评分对 `zip(params.categories, coverage.categories, strict=True)`
  过滤 `failed_keys` 后再平均。
- `blindspot/grid.py`：`compute_blindspot` 增加 `types` 注入参数，docstring 声明
  "只应包含本次实际检索成功的类目"。
- `models/report.py`：`overall_score` 描述补充"POI 检索失败的类目不计入平均"；
  前端 types/analysis.ts 注释同步。
- 前端 `ReportPanel.tsx`：`blindspot.types` 为空时显示"未参与盲区判定"而非
  "未识别出盲区"（避免类目全被过滤时的最后一个误导文案）。

## 验证结果

```
tests/unit/test_task_manager.py::test_poi_single_category_failure_degrades PASSED
tests/unit/test_blindspot_grid.py::test_types_injected_skips_unsearched_category PASSED
============================== 6 passed in 2.52s ===============================   （定向回归集）

$ python3 -m pytest -q --cov=app.isochrone --cov=app.poi --cov=app.coverage --cov=app.blindspot --cov-fail-under=95
Required test coverage of 95% reached. Total coverage: 97.93%
99 passed, 2 warnings in 12.06s
```

降级单测断言：medical 失败时 `report.blindspot.types == ["education", "shopping"]`、
`overall_score == round((教育+购物+养老)/3, 1)`（不含 medical 的 0 分）。

## 影响范围与注意事项

- 波及：所有含类目降级或部分勾选的分析报告；前端仪表盘/盲区摘要展示。
- 设计取舍：**真实检索成功但 0 设施**的类目仍判全域缺失、仍计 0 分（docs/02 §3.4
  的显式口径，未改动）；只排除"没有数据"的类目。
- 复发信号：报告 blindspot.types 含有未在 params.categories 中或降级标记中的类目；
  overall_score 与 categories 分值手工平均不一致（排除失败类目后应一致）。
- 回归测试节点：`test_poi_single_category_failure_degrades`（unit/test_task_manager.py）、
  `test_types_injected_skips_unsearched_category`（unit/test_blindspot_grid.py）。
