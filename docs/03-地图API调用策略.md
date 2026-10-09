# 地图 API 调用策略

> 交付文档 ①（docs/01 §8）。本文描述**已实现**的百度地图 Web 服务 API 调用体系，
> 与 docs/02 §3.4 的设计互为对照：设计文档讲"为什么"，本文讲"实际是什么、每个数字
> 从哪来"。所有配置项默认值见 `backend/app/core/config.py`，可经环境变量（前缀
> `OPENMAP_`）覆盖。

---

## 1. 总纲：唯一出站点 + 端口-适配器

全部出站 HTTP 收口在 `backend/app/mapapi/` 一个包内，域层（isochrone/poi/coverage/
blindspot）与路由层对故障细节完全无感（CLAUDE.md 架构铁律 #3）。

```text
api/ → tasks/ → 域层(纯函数) → MapProvider 协议 ←── BaiduClient（百度适配器）
                                          ↖── ReplayProvider（演示/CI 回放）
```

`MapProvider`（`mapapi/provider.py`）是唯一地图能力协议——5 个方法覆盖本项目全部
外呼需求，**新增地图能力 = 先扩展协议**：

| 方法 | 用途 | 调用量级（单次分析） |
|------|------|---------------------|
| `geocode(address, city)` | 地址 → BD09 坐标 | 0~1（前端地图点选则 0） |
| `search_pois(query, center, radius, page_size, max_pages)` | 圆形区域 POI 检索（内部翻页聚合） | 5（四大类各一次 + 教育第二检索词"学校"，BF-010） |
| `route_matrix(origin, destinations)` | 单源批量步行时长矩阵（内部按批量上限分片） | 5~6（等时圈 4~5 + 覆盖边缘带 1） |
| `walking_route(origin, destination)` | 单 OD 步行规划（降级链第二级专用） | 0（矩阵健康时不出站） |
| `close()` | 释放连接池 | — |

协议同时定义错误分类 `ErrorKind`（10 类）与"哪些算瞬时错误"（`RETRYABLE_KINDS =
{TIMEOUT, SERVER, RATE_LIMIT}`）——这是重试与熔断共用的判定口径，收在协议层保证
两个机制永不漂移。

## 2. 端点契约（四个百度端点的实测口径）

实现：`mapapi/baidu/client.py`。坐标系全程 BD09（百度原生，入口转换零成本）。

### 2.1 地理编码 `/geocoding/v3/`

- 参数：`address`（+可选 `city`）；`output=json`、`ak` 由公共层统一附加。
- 依赖 v3 默认返回 `bd09ll`，不传坐标类型参数。`result.location` 为空 → 返回 `[]`，
  视为"无匹配"而非错误。

### 2.2 地点检索 `/place/v3/around`（v3，注意与 v2 的三处差异）

- `location` 参数是 **`lat,lng` 顺序**（v2 是 `lng,lat`，BF-004 实测踩坑）；
- `radius` 只是召回权重，必须加 `radius_limit=true` 才硬约束半径；
- 顶层无 `tag` 字段，分类标签在 `scope=2` 时的 `detail_info.classified_poi_tag`。
- 分页：`page_size ∈ [10,20]`、`page_num ∈ {0,1,2}`——**越界页静默返回空**
  （BF-005 实测），故在配置层用 pydantic `Field(ge=..., le=...)` 强制校验而非静默容错。
  翻页循环按"短页即停"终止：`len(page) < page_size → break`。

### 2.3 批量步行矩阵 `/routematrix/v2/walking`

- `origins`/`destinations` 格式：`lat,lng`（6 位小数）`|` 分隔，单源多目的地；
- 目的地按 `matrix_batch_size`（默认 50）分片，每片独立走完整请求管线；
- **1:1 行数契约**：响应 `result` 行数必须等于目的地数，缺行抛 `SERVER` 错误而非
  静默错位（BF-006：上游会省略不可步行目的地的行）；
- 不可达的表现是行内 `distance/duration` 的 `value` 为 `null` → 转 `RouteLeg(None,
  None)`，不是错误。

### 2.4 单 OD 步行规划 `/direction/v2/walking`

- 降级链第二级专用（覆盖边缘带 ≤30 条，矩阵失败时逐条兜底）；
- 响应数值兼容**裸数字与 `{value: x}` 包装**两种形态（两代接口口径都存在于线上，
  降级路径尤需宽容）；`routes` 为空 → 不可达 `RouteLeg(None, None)`。

### 2.5 SN 签名与鉴权

- AK 白名单制为默认；选择 SN 校验时（`OPENMAP_BAIDU_SK` 非空）：
  `sn = MD5(quote(path?query, safe="/?=&%") + sk)`，官方文档口径（`_calc_sn`）。
- 密钥纪律：服务端 AK/SK 只存后端环境变量，日志与 git 历史零泄漏（前端用独立
  `VITE_BMAP_AK`，双 Key 体系）。

### 2.6 status 码 → 错误分类映射

`_DEFAULT_STATUS_KIND`（可被 `OPENMAP_STATUS_KIND_OVERRIDES` 覆盖，换认证等级后
配额语义变化时无需改码）：

| 百度 status | 含义 | ErrorKind | 可重试 | 熔断证据 |
|------------|------|-----------|--------|---------|
| 1 | 服务内部错误 | SERVER | ✅ | ✅ |
| 2 | 请求参数非法 | BAD_REQUEST | ❌ | ❌ |
| 3 / 5 / 210 | AK 无效 / 非法 / IP 校验失败 | AUTH | ❌ | ❌ |
| 4 | 配额校验失败 | QUOTA | ❌ | ❌ |
| **302 / 401** | **当前并发量已超约定并发配额** | **RATE_LIMIT** | ✅ | ✅ |
| 其他非 0 | 未收录 | UNKNOWN | ❌ | ❌ |

> 302/401 是 BF-009（2026-10-09）的实测结论：个人认证 AK 的批量接口并发限流返回
> 401（同义码 302），曾因未收录归 UNKNOWN 不可重试，导致 QPS=2 稳态下第 5 批矩阵
> 直接失败、等时圈整体降级。收录后 0.5s 退避一次即自愈。**未收录码坚持归 UNKNOWN
> 不可重试**是刻意的保守取向：误重试 AUTH/QUOTA 只会放大故障。

## 3. 出站管线：六道闸门顺序固定

`BaiduClient._request` 的执行顺序（`mapapi/baidu/client.py`）：

```text
① 缓存查命中 ──命中──→ 零出站返回（记 cache_hits，不扣预算）
      │未命中
② QuotaBudget.acquire ──超限──→ BUDGET_EXCEEDED 本地拦截（零出站）
      │
③ CircuitBreaker.allow ──打开──→ BREAKER_OPEN 本地拦截（零出站）
      │
④ 令牌桶 acquire（排队等待，不丢弃）
      │
⑤ HTTP 出站（超时 8s）→ 状态分类 → json 解析 → status 分类
      │成功
⑥ 写缓存（TTL 7 天）→ 记录成功（熔断器归零）
```

各环节参数（默认值均可配）：

| 机制 | 默认值 | 设计依据 |
|------|--------|---------|
| 缓存 TTL | 7 天 | POI 与测时数据日内稳定；LRU 容量 4096（内存）/ Redis `ex` |
| 缓存键 | 参数规范化 JSON → MD5 | 坐标在调用侧取整 6 位小数（≈0.1m）后入键，保证命中率 |
| 单分析预算 | 40（硬上限） | docs/02 §3.1.3 预算表上限的护栏值；实际 ≈11 次（见 §5） |
| 熔断阈值/开断 | 连续 5 次瞬时失败 / 30s | 半开单探针 + `abandon_probe` 防取消卡死 |
| 令牌桶 | QPS 2 / 突发 2 | 压在 AK 级并发配额内（BF-003/BF-009 两轮实测收敛） |
| 重试 | 3 次（含首试），指数退避 0.5s 基数 | 仅 `RETRYABLE_KINDS`；重试也占预算（物理口径） |

**计数口径**（QuotaBudget，`core/budget.py`）：预算计**物理出站 HTTP**——含批量
分片与重试；缓存命中与熔断/预算本地拦截不计。contextvar 按任务隔离，`asyncio.
gather` 子任务自然继承。集成测试用 respx 物理拦截计数与 `http_calls` 字段**双口径
互证**（`tests/integration/test_analysis_flow.py::test_full_analysis_outbound_
within_budget`），计数漂移（如缓存命中误扣）会立即红。

**熔断语义补充**：只累计瞬时错误（TIMEOUT/SERVER/RATE_LIMIT）；任何一次成功即闭合
归零——百度的并发限流是窗口性的，快速恢复是常态而非例外。

## 4. 三级降级链（矩阵故障时的自动回退）

```text
第一级：批量矩阵（正常路径）
   │ MapApiError / 契约违反
   ▼
第二级：逐条 walking_route（仅覆盖判定边缘带 ≤30 条；预算名额不足以覆盖全部时
        整层跳过——不做半截采样，避免"按失败时机选择实测对象"的选择偏差）
   │ 仍失败 / 预算不足
   ▼
第三级：直线距离 × 绕行系数 1.3 ÷ 步速 1.35 m/s 本地估算
        confidence 封顶 0.3，method 切换 straight-estimate，报告声明 degraded_reason
```

- 等时圈阶段矩阵失败**直接落第三级**（64+ 探针逐条会爆预算）；
- POI 类目失败**不降级清零**：显式 `poi:<key>:unavailable` 标记，该类不计入综合
  评分与盲区判定（BF-007：数据缺失 ≠ 事实结论）；
- 全部降级标记（5 种）在前端 `constants/degraded.ts` 有对应文案，未登记的 key
  原样展示以便发现遗漏。

## 5. 调用预算：设计表 vs 实测

docs/02 §3.1.3 预算表（上限估算）与 2026-10-09 真实采集实测（docs/07）：

| 步骤 | 预算表 | 实测（交道口/回龙观） |
|------|--------|---------------------|
| 地理编码 | 0~1 | 1（脚本入口地址解析） |
| 等时圈 A+B 矩阵 | 5~6 | 4~5 批 |
| 等时圈 D 加密 | 0~1 | 0~1 批（回龙观触发 5 探针） |
| POI 检索 | 10~20 | 4（每类 1 页即短页停；教育双检索词为 BF-010 后口径，预计 +1） |
| 覆盖边缘带矩阵 | 1~2 | 0~1 批 |
| **合计** | **≈15~26** | **11 / 11**（含 401 自愈重试各 1；采集于 BF-010 之前） |

朴素方案（逐 POI 逐方向路径规划）需 300+ 次——批量矩阵 + 攒批二分把调用压低一个
数量级以上，这是 30% 工程优化维度的核心论据。

## 6. 演示模式（评审保底）

`OPENMAP_DEMO_MODE=1`（或未配置 AK）→ `ReplayProvider` 回放 `data/replays/demo.json`：

- 快照含 `geocode`/`poi_pool`/`isochrone profile`（16 方向绕行系数与上限），
  矩阵与步行规划共享同一米制空间模拟器——**降级切换不改变语义**；
- 全链路零配额，CI 与评审演示同源；
- **纪律**：新增 Provider 方法必须同步扩展回放实现与快照数据并用回放断言行为
  （CLAUDE.md 强制，CI 回放测试守门）。

## 7. 已踩坑索引（本策略的"证据链"）

| 编号 | 教训 → 落点 |
|------|------------|
| BF-003 | 并发超免费档触发百度短信告警 → 令牌桶默认压到 2/2 |
| BF-004 | v3 place 检索 lat,lng 顺序与静默空返回 → 适配器逐字段对齐 v3 契约 |
| BF-005 | v3 越界页静默空 → 配置层强制分页范围 + 短页即停 |
| BF-006 | 矩阵响应缺行静默错位 → 1:1 行数契约抛错 |
| BF-009 | 并发限流码 401/302 未收录 → RATE_LIMIT 可重试自愈 |

详见 `docs/bugfix/`。每条都有 `@pytest.mark.regression` 回归测试钉住，CI 强制
索引与测试双向一致（`scripts/check_bugfix_regression.py`）。
