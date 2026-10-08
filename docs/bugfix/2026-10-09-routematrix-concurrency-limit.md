# BF-009 百度并发限流码（401/302）未收录：批量矩阵直接失败，等时圈被迫整体降级

- 日期：2026-10-09 　提交：d6aa36c 　影响模块：`mapapi/baidu/client.py`（status 分类）、`core/config.py`（burst 默认）

## 现象

M5 真实社区采集（`scripts/collect_samples.py`，真实 AK）时，批量矩阵全批失败：

```
2026-10-09 01:19:42 [warning] isochrone.matrix_degraded
    error='[UNKNOWN] 当前并发量已经超过约定并发配额，限制访问' reason=matrix:unavailable
```

- `retries=0`：错误归 `UNKNOWN` 不可重试，一次失败即放弃；
- 等时圈整体降级 `straight-estimate`（直线×1.3 模型估算），两社区均失去实测口径；
- 修复映射后复跑又暴露次生现象：两社区仅隔 ~5s 背靠背运行时，第二个社区矩阵连续
  401（重试 3 次耗尽）→ 熔断打开 → 30s 内 **POI 四类目检索全部 `BREAKER_OPEN`
  快速失败**，`poi:*:unavailable` 连环降级、`overall_score=None`。

## 根因分析

定位过程按"逐步收窄变量"推进（每步真实 API 各 3~8 次探测）：

1. **排除端点/AK 故障**：单发、1.5s 间隔连发均 `status=0` 成功——接口与密钥正常；
2. **排除请求数并发**：3 个并发请求（2 目的地）全部成功——不是"同时几个请求"的问题；
3. **排除批量上限**：50 目的地 × 1.5s 间隔全部成功——`matrix_batch_size=50` 本身合法；
4. **复现**：50 目的地**背靠背连发**（无间隔），第 5 个触发，拿到真实响应：

   ```json
   {"status":401,"message":"当前并发量已经超过约定并发配额，限制访问","num":50,"mode":"walking"}
   ```

结论分两层：

- **主因**：百度批量接口的并发限流状态码是 **401**（文档同义码还有 302），均未收录于
  `_DEFAULT_STATUS_KIND` → 归 `UNKNOWN`（不可重试、不构成熔断证据）。而令牌桶 QPS=2
  是**平均速率**语义，滑动窗口内瞬时密度可达 3+/s，稳态连打第 4~5 批矩阵即落入
  百度 AK 级并发窗口——本应"退避 0.5s 重试一次自愈"的场景被放大成"直接降级"。
- **次因（采集工具边界）**：并发配额按 **AK 级滚动窗口**跨请求残留，背靠背第二个
  社区叠加前一社区余波 → 连环 401 耗尽重试 → 熔断打开殃及无关端点（POI 检索）。
  熔断按 Provider 级单例是设计语义（配额确实 AK 级共享，拦住出站反而保护配额）；
  真实用户不会 5s 内连续发起两个社区分析，属采集脚本未隔离的边界场景。

## 修复方案

| 改动 | 内容 |
|------|------|
| `mapapi/baidu/client.py` | `_DEFAULT_STATUS_KIND` 增 `302/401 → RATE_LIMIT`（可重试 + 熔断证据），注释记录实测依据与复发信号 |
| `core/config.py` | `api_burst` 默认 3.0 → 2.0：压低瞬时叠加（geocode + 矩阵首批同秒 3 发是触发形态之一） |
| 根 `.env`（本地，不入库） | `OPENMAP_API_BURST=3 → 2`，与新默认对齐 |
| `scripts/collect_samples.py` | `--gap 20`：多社区采集间隔冷却 AK 并发窗口（次因的采集侧规避） |

取舍说明：不改熔断粒度（不按端点拆分熔断器）——百度并发配额本就是 AK 级共享，
Provider 级熔断语义正确；把 QPS 默认降到 1 也能缓解，但牺牲正常吞吐，且退避重试
已足够自愈（见验证），故保留 QPS=2。

## 验证结果

回归测试（respx 模拟 302/401 → 第二次成功，断言重试发生）：

```
$ python3 -m pytest tests/integration/test_baidu_client.py -q
...................                                                       [100%]
19 passed in 8.12s
```

真实采集最终结果（两社区均实测口径、零降级，`retries=1` 即 401 退避一次自愈的直接证据）：

```
[jiaodaokou] analysis.completed api_calls={'route_matrix': 5, 'search_pois': 4,
    'http_calls': 11, 'cache_hits': 0, 'retries': 1} degraded=[]
[jiaodaokou] isochrone: method=ray-spline-v1 ... 15min=1.72km²  overall_score=15.3
[huilongguan] analysis.completed api_calls={'route_matrix': 5, 'search_pois': 4,
    'http_calls': 11, 'cache_hits': 0, 'retries': 1} degraded=[]
[huilongguan] isochrone: method=ray-spline-v1 ... 15min=1.23km²  overall_score=12.8
```

## 影响范围与注意事项

- 影响所有真实模式的矩阵/步行规划容错路径；演示模式（回放）无限流，不受影响。
- `status_kind_overrides` 仍可覆盖默认映射（换 AK 认证等级后配额语义变化时用）。
- **复发信号**：日志出现 `isochrone.matrix_degraded` 且 `error=[UNKNOWN]` 含
  "并发/配额"字样 → 映射表又漏了新的限流码，先探测真实响应再补表。
- 回归测试：`tests/integration/test_baidu_client.py::test_concurrency_limit_status_retried_then_ok`
  （`@pytest.mark.regression`，BF-009，302/401 参数化）。
