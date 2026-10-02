# BF-005 v3 迁移遗留 v2 分页参数：越界页静默空返回（浪费配额 + 召回封顶 60）

- 日期：2026-10-02 　提交：（随本档同提交，hash 见 git log） 　影响模块：backend/app/core/config.py

## 现象

无明显报错（这正是问题所在）。BF-004 将地点检索迁移到 /place/v3/around 时，
分页参数 `page_size=20 / max_pages=5` 原样沿用 v2 时代配置。代码评审发现两类
静默失真（后经真实 AK 实测证实）：

1. 类目命中 ≥60 条时，循环发出 `page_num=3`、`page_num=4` 两次超出 v3 契约的
   请求，服务端返回 `status=0` + 空结果——不报错、不重试，每稠密类目白耗 1 次
   配额调用（4 类目最多 8 次/分析，对照 docs/02 §3.1.3 的 POI 配额 8–16 次预算）；
2. 单类目召回被静默封顶 60 条（3 页 × 20），低于 5×20=100 的代码设计值，覆盖
   评分失真且无从察觉。

## 根因分析

定位过程：评审 Angle-Efficiency 对 `git diff e0f4101..HEAD` 逐行核对时提出
"分页契约未随迁移重推导"的怀疑，随后按官方文档 + 真实 AK 探测双重验证：

- **官方文档**（地点检索 3.0 接口文档，`page_num` 参数）：取值仅
  "0、1、2"；`page_size` 取值 10-20（默认 10、最大 20）。
- **真实 AK 实测**（项目 AK、SN 签名，中关村"美食"1.3km，total=41）：
  - `page_num=0` → 20 条；`page_num=2` → 1 条；
  - `page_num=3`、`page_num=4` → `status=0`，0 条（静默空，**不是**错误码）；
  - `page_size=30` → 返回 20 条（服务端静默钳制，无告警）。
- **为何此前没暴露**：BF-004 的真机验证只遇到稀疏结果（各类目 7/3/4/0 条，
  首页即短页提前 break），多页翻页路径从未被真实执行；mock 测试又按请求值
  满页返回，覆盖不到契约边界。

根因：v2→v3 迁移只核对了请求参数格式（location 顺序、radius_limit、tag 结构），
未重新推导分页契约；且 `poi_page_size` 无边界校验，越界配置会被服务端静默钳制，
进而让 `len(page) < page_size` 的末页判定在首页误判（配 30 得 20，首页即 break）。

## 修复方案

`backend/app/core/config.py`（约束按铁律#4 收口到配置层，客户端循环逻辑不动）：

- `poi_max_pages` 缺省 5 → 3，并加 `Field(ge=1, le=3)` 硬校验；
- `poi_page_size` 加 `Field(ge=10, le=20)` 硬校验（默认仍 20）；
- 注释由"百度 place v2 单页上限"更新为 v3 口径（page_num 0-2 / page_size 10-20）。

取舍：越界配置**启动即报错**而非静默钳制——覆盖截断这类失真必须"响"，否则
与 status=0 空结果同样不可诊断。新增 `backend/tests/unit/test_config.py`
（7 用例）钉住默认值与两条边界，防配置回退。

## 验证结果

后端四链（WSL，`backend/`）：

```text
$ python3 -m ruff format --check . && python3 -m ruff check . && python3 -m mypy && python3 -m pytest -q
42 files already formatted
All checks passed!
Success: no issues found in 34 source files
.................................................................        [100%]
65 passed, 2 warnings in 4.66s
```

（65 = 修复前 58 + 新增 7：默认值断言 1 + page_size 越界参数化 3 + max_pages
越界参数化 3。越界用例断言 `pydantic.ValidationError`，如 `poi_max_pages=5`
启动即被拒——即本次缺陷的复发信号测试。）

修复前根因证据（真实 AK 实测，见"根因分析"）：`page_num=3` → `{"status": 0,
results: []}`；`page_size=30` → 实返 20 条。

## 影响范围与复发信号

- 影响面：真实模式 POI 检索召回上限由"名义 100 / 实际 60"修正为"契约内 60"，
  稠密类目每分析最多省 4 次配额调用；回放模式池内每查询 ≤4 条，行为不变；
  `search_pois` 签名与循环逻辑未动，调用方无感。
- 配置迁移：`.env` 若显式设过 `OPENMAP_POI_MAX_PAGES>3` 或
  `OPENMAP_POI_PAGE_SIZE∉[10,20]`，升级后启动即校验失败，按新边界改值即可。
- 复发信号：`tests/unit/test_config.py` 的 7 个用例；若百度放宽 page_num 上限，
  同步放宽 `le=3` 并回归真机多页验证（勿只改文档）。
