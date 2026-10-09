# BF-010 「实验小学」类校名不被 query=小学 召回，教育类目大面积漏设施

- 日期：2026-10-09 　提交：<hash 待回填> 　影响模块：`app/poi/service.py`、`data/config/poi_taxonomy.json`、`data/replays/demo.json`

## 现象

用户实测（杭州市和宁文华府，中心点 120.110885,30.342059）报告：「和宁文华府北门
正对门就是安吉路小学新文小区，但检索结果教育（小学）一栏没有它」。同批次分析中
922 m 内的九年一贯制学校（杭州绿城育华亲亲学校，含小学部）同样缺席。

## 根因分析

检索链路三层（召回 → 白名单过滤 → 去重）中定位缺失环节：用一次性诊断脚本
（真实模式，同中心分别以不同 query 调 `/place/v3/around`，打印原始召回）逐层排查：

```text
query='小学'   r=1300 → 4 条（最近 968m，无安吉路）
query='实验小学' r=1300 → 0 条
query='学校'   r=1300 → 21 条，含 237m「杭州市安吉路新文实验小学西门」tag=出入口;门
query='安吉路新文' r=1300 → 29 条，学校主体 POI 仍缺席，仅上述西门门禁可召回
```

结论：**召回层缺失，清洗层无责**（门禁 POI 名称含"小学"，若被召回必过白名单）。
根因是百度 v3 周边检索的分词索引行为：

1. 「实验小学」是复合词，`query=小学`/`query=实验小学` 均不命中该校（对照组：
   「莫干山路小学」「沈括小学」等普通校名可命中）；
2. 该校主体 POI 在周边检索索引里**完全不可召回**（名称前缀 query 也不返回），
   唯一线索是携带校名的**门禁 POI**；
3. 九年一贯制学校（含小学部）名称不含"小学"、tag 为 `教育培训;九年一贯制学校`，
   单检索词「小学」的召回与白名单双双不覆盖。

## 修复方案

召回补全 + 计数防虚高，三层配套（文件级）：

- `app/poi/service.py`：`CategorySpec.query: str` → `queries: tuple[str, ...]`，
  `search_categories` 类目内多检索词顺序检索后合并（跨词重复由既有 uid 去重收口）；
  `clean_records` 新增第 ④ 步**附属 POI 坍缩**——tag ∈ {出入口, 校内设施} 的门禁/
  楼栋条目按主体名（剥离「-南门」「西门」「教学楼-3幢」后缀）分组，主体在场剔附属，
  主体缺席保留最近一个附属作为该校代表（安吉路场景的唯一召回线索）。
- `data/config/poi_taxonomy.json`：education `queries: ["小学", "学校"]`、
  `filter_pattern: "小学|九年一贯制"`（九年一贯制经 tag 分支收编，纯中学仍剔除）。
- `data/replays/demo.json`：回放池同步——既有小学条目补「学校」双标（对齐真实百度
  行为），新增 4 条构造样本：`demo-edu-004`（实验小学西门，主体缺席）、`demo-edu-005`
  （九年一贯制）、`demo-edu-002-gate`（同校门禁坍缩）、`demo-edu-noise2`（中学噪音）。

取舍：教育类目 +1 次逻辑检索调用（换召回完整性），单次分析调用口径 4→5、预算表
合计 ≈15~25 → ≈15~26，远低于硬上限 40；docs/02 §3.1.3 / docs/03 预算表已同步。

## 验证结果

修复后全量验证（WSL，backend/）：

```text
$ python3 -m ruff format . && python3 -m ruff check . && python3 -m mypy && python3 -m pytest -q
1 file reformatted, 66 files left unchanged
All checks passed!
Success: no issues found in 50 source files
152 passed, 2 warnings in 24.44s    # 原 149 + 新增 3 项教育召回回归
```

**真实 API 原地复核**（报障地点 120.110885,30.342059，走完整多检索词 + 清洗管道）：

```text
education 清洗后 7 条：
     237m  杭州市安吉路新文实验小学西门  | tag='出入口;门' <-- 报障目标（修复前缺席）
     922m  杭州绿城育华亲亲学校  | tag='教育培训;九年一贯制学校'   （修复前缺席）
     968m  杭州市莫干山路小学(祥符校区)  | tag='教育培训;小学'
    1017m  杭州永正实验学校  | tag='教育培训;九年一贯制学校'       （修复前缺席）
    1021m  沈括小学(通运校区)  | tag='教育培训;小学'
    1265m  杭州市长阳小学  | tag='教育培训;小学'
    1277m  杭州大关小学教育集团(祥符校区)  | tag='教育培训;小学'
```

修复前该地点教育类目仅 4 条（最近 968m）；修复后 7 条，报障学校以最近门禁为代表
返回，原有 4 所普通小学无回归。

回放层端到端口径（`test_api_demo_flow`，断言教育类目清洗后）：

- 「中关村实验小学西门」在场（主体缺席，门禁为代表）✓
- 「中关村科学城学校」（九年一贯制，tag 命中）在场 ✓
- 「中关村第二小学-南门」被坍缩剔除、主体只计一次 ✓
- 中学类噪音零进入 ✓

根因诊断脚本与原地复核脚本（真实 API 直查）用后均已删除，未入库。

## 影响范围与注意事项

- 波及：所有类目的检索编排（仅 education 配置了双词，其余类目单词语义不变）；
  `api_call_stats["search_pois"]` 逻辑调用口径 4→5（预算断言已同步）。
- 复发信号：教育类目在「实验小学」密集城市召回偏少；或某类目设施数虚高
  （同校门禁未坍缩）。百度分词行为属黑盒，若后续「实验学校」「外国语学校」
  等校名仍漏召回，扩 `queries` 即可（改 JSON 零改码）。
- 回归测试节点（`@pytest.mark.regression`，docstring 注明 BF-010）：
  `tests/unit/test_poi_clean.py::test_gate_only_school_kept_as_representative`、
  `tests/unit/test_poi_clean.py::test_gate_collapsed_when_main_present`、
  `tests/unit/test_poi_clean.py::test_nine_year_school_kept_by_tag_and_middle_school_filtered`；
  回放端到端断言见 `tests/integration/test_demo_flow.py::test_api_demo_flow`。
