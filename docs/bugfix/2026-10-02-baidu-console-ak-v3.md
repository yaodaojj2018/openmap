# BF-004 新控制台 AK 地点检索静默返回空 + routematrix 坐标顺序错误

- 日期：2026-10-02 　提交：19755aa 　影响模块：`mapapi/baidu/client`（search_pois / route_matrix）

## 现象

真实模式下地理编码正常（杭州城北万象城精确命中），但"检索周边设施"四类目全部 0 条（HTTP 200，非报错）。
更诡异的是：连"万象城"自身在 1km 内、泛词"小区"在 2km 内都是 0 条——任何城市位置都不可能如此。

## 根因分析

隔离测试逐步收窄（关键：用同一 AK 分别测不同端点/不同城市）：

| 测试 | 结果 |
|---|---|
| v2 圆形检索 @ 中关村（M1 验证过的点） | status=0，**0 条** → 排除"该位置没数据" |
| v2 城市检索 region=杭州 | status=0，5 条 → **AK 的地点检索服务是开通的** |
| v2 矩形检索 bounds | **status=9**（错误码）→ v2 空间类检索整体异常 |
| v3/around @ 杭州 | status=0，5 条 ✓ |

结论：**2025 控制台升级后新发的 AK，地点检索权限挂在 3.0 版**——v2 的圆形/矩形检索对其静默返回空或报错，
只有 v2 城市检索残留可用。老代码 `place/v2/search` 圆形检索正好踩中最坏路径：不报错、给空集，
上层无法区分"没数据"与"没权限"。

连带发现（验证 v3 时顺手测出的第二 bug）：`routematrix/v2/walking` 坐标串我们按"经度,纬度"拼，
该接口实为**纬度在前**（官方示例 `origins=40.45,116.41`）→ status=2 参数非法。
意味着真实模式下等时圈也会全链路失败（此前只演示过回放模式，未暴露）。

## 修复方案

`mapapi/baidu/client.py`：

1. `search_pois`：`/place/v2/search` → **`/place/v3/around`**，三处适配——
   - `location` 参数改 `纬度,经度`（与 v2 相反！）
   - 补 `radius_limit=true`：v3 的 radius 只是召回权重，必须显式严格限定（生活圈覆盖语义要求）
   - 分类标签从顶层 `tag` 改为 `detail_info.classified_poi_tag`（scope=2 时返回）
2. `route_matrix`：`_fmt_points` 改 `lat,lng|lat,lng`
3. 回放 Provider / 域层零改动（坐标格式只存在于 HTTP 边界，验证了端口-适配器分层的价值）

测试：respx mock 同步迁移 v3 响应结构，并新增**坐标顺序回归断言**（location/radius_limit/origins）。

## 验证结果

真实 AK 端到端（杭州城北万象城）：

```
POI medical=7 education=3 shopping=4 elderly=0（该新城区 1.3km 内确无养老院，真实结果）
ISOCHRONE: 200 levels=[5,10,15] batches=5 probes=112
  5min 0.1247 km² conf=1.0 / 10min 0.3918 km² conf=1.0 / 15min 0.8896 km² conf=1.0
```

pytest 58 passed（含坐标顺序回归）。

## 影响范围与注意事项

- **凡引用百度 Web API 的地方，坐标参数顺序逐接口核对**：place v2=lng,lat、place v3=lat,lng、
  routematrix=lat,lng——没有全局约定，只能逐个看当期文档（铁律 #4 的"以当期官方文档为准"）
- **静默空结果是最危险的失败模式**：status=0 + 空 results 无法与真没数据区分，
  跨版本迁移时必须用"已知有数据的锚点"（如中关村的药店）做隔离测试
- 复发信号：某检索端点全网任何位置都返回空 / routematrix 报 status=2
- 后续若再遇控制台策略变化（如 routematrix 也出 v3），按本档的隔离测试法定位
