# 回归测试与 CI 门禁设计

> 目的：把"AI 改代码 → 回归验证 → 人工复核"变成仓库里的机械约束，而不是口头纪律。
> 对应交付清单（docs/01 §8）："自动化测试脚本（算法单测 + API mock 测试）"与"CI/CD"两项。
> 本文是测试/CI 层面的事实来源；架构层面仍以 docs/02 为准。

## 1. 回归工作流公式 → 本仓库机制映射

回归范围不是"全量重跑"，按下式生成：

```
回归范围 = f(改动影响模块, 历史 Bug, 风险等级, 业务链路) → 回归清单 → 人工复核
```

| 公式步骤 | 本仓库机制 | 载体 |
|---|---|---|
| ① 改动 → 影响模块 | 依赖单向铁律（`api → tasks → 域层 → mapapi → core`）天然切出影响面：改哪层跑哪层测试 | 架构分层 + 分层测试命令（§4） |
| ② 历史 Bug → 回归范围 | `docs/bugfix/` 每个 BF-XXX 必须对应至少一个 `@pytest.mark.regression` 测试，BF 编号写进 docstring；CI 强制校验索引表 ↔ 测试引用一致 | `scripts/check_bugfix_regression.py` |
| ③ 风险等级 → 优先级 | pytest marker 分层：`smoke`（主链路 API 冒烟，秒级）→ `regression`（BF 对应）→ 全量。冒烟前置，红了不等全量 | `pyproject.toml` markers + CI 步骤顺序 |
| ④ 业务链路补遗漏 | 主链路 geocode → isochrone → poi → (coverage → blindspot) 的 E2E = 回放模式测试（零配额）；新增 Provider 方法必须同步扩展回放快照（CLAUDE.md 既有规则） | `tests/integration/test_demo_flow.py` 等 |
| ⑤ 输出回归清单 | CI Job Summary：跑了哪些回归测试（BF 对照）、域层覆盖率、失败详情；一致性检查打印 BF↔测试映射表 | `GITHUB_STEP_SUMMARY` + 检查脚本输出 |
| ⑥ 人工复核 | branch protection：required status checks + ≥1 review 才能合并；AI 生成的回归用例，断言与边界条件须在 PR 中被人对照 BF 档案确认 | GitHub 仓库设置（§7，需人工配置） |

## 2. 基线：BF 归档 ↔ 测试对照（2026-10-03 摸底）

| BF | 主题 | 回归测试 | 基线状态 |
|---|---|---|---|
| BF-001 | 等时圈回放畸变（地球模型混用） | `test_isochrone.py::test_probe_radius_matches_haversine` | ✅ 已有，docstring 未写 BF 编号 |
| BF-002 | 根目录 .env 双端不生效（env_file 相对路径） | `test_config.py::test_env_file_anchored_to_repo_root` | ❌ 当时仅人工验证，本次补 |
| BF-003 | 地点检索并发超限（默认 qps/burst 超契约） | `test_config.py::test_rate_limit_defaults_match_free_tier` | ❌ 当时仅人工验证，本次补 |
| BF-004 | v3 迁移 + 坐标顺序 | `test_baidu_client.py::test_poi_pagination_aggregates`、`::test_route_matrix_parses_and_formats_params` | ✅ 已有断言，docstring 未写 BF 编号 |
| BF-005 | v3 分页契约越界静默失真 | `test_config.py` 全部 7 用例 | ✅ 已有且已注明 BF-005 |

结论：测试本身大多在（修复时同步写了），缺的是**编号关联与门禁强制**——这正是脚本要钉住的部分。

## 3. 门禁流水线（PR 时序）

```
PR 提交/更新
 ├─ backend job
 │   ├─ ruff format --check / ruff check / mypy     # 静态最快失败（既有）
 │   ├─ check_bugfix_regression.py                  # ② BF↔测试一致性
 │   ├─ pytest -m smoke                             # ③ 冒烟先行
 │   └─ pytest -q --cov=app.isochrone --cov=app.poi
 │         --cov-fail-under=95 --junitxml=pytest.xml # 全量 + 域层覆盖率门禁
 ├─ frontend job（lint + test + build）
 │   └─ vitest run                                  # 前端回归（BF↔测试一致性含前端 seam）
 └─ merge 条件 = 上述全绿 + ≥1 human review
main push → docker job（compose 校验 + 镜像构建，既有）
```

## 4. 关键取舍

1. **不引入 testmon / pytest-picked**。当前 65 用例全量 10s 内跑完，"改动 → 影响模块"用
   架构分层 + 目录约定解决（`pytest tests/unit` vs `pytest tests/integration` vs 全量）；
   等用例规模上百再评估 testmon，现在上是负资产。
2. **覆盖率门禁只作用于域层**（`app.isochrone` + `app.poi`，M3/M4 落地后扩到
   `coverage`/`blindspot`）。域层是纯函数、评分核心、好测；全局阈值会逼着给适配器写
   凑数测试。阈值 95 = 基线 97% 向下留 2 个点余量的棘轮：防倒退，不做 aspirational 目标；
   只允许单调上调，下调必须走 docs/02 变更说明。
3. **BF 一致性检查是轻脚本不是框架**。解析 `docs/bugfix/README.md` 索引表中的 BF 编号，
   确认每个都能在**测试源码**中 grep 到引用（后端 `backend/tests/**.py` 或前端
   `frontend/tests/**.ts(x)`），反向报告孤儿引用。纯前端缺陷（如任务状态机）的回归
   seam 在前端 store/hook，不应为过门禁在后端伪造弱 seam——故引用来源含前端 vitest。
   零依赖（标准库），失败信息直接给出缺口，不产出需要二次解读的报告。

## 5. 标记规范与本地命令

marker 在 `backend/pyproject.toml` 注册，`addopts = "--strict-markers"` 禁止未注册标记：

- `smoke`——主链路 API 最小验证（health / geocode / pois / isochrone 端点），任何 PR 必须绿；
- `regression`——BF 归档对应的回归用例，docstring 首行含 `（BF-XXX）`；
- 无标记——普通单元/集成用例，只在全量跑。

```bash
# 本地（WSL，backend/ 下）
python3 -m pytest -m smoke -q        # 改完先跑：秒级、主链路
python3 -m pytest -m regression -q   # 再跑：历史 Bug 防复发
python3 -m pytest -q                 # 最后全量（AI 改动的默认终点）
python3 scripts/check_bugfix_regression.py   # 动了 bugfix 归档或测试引用时

# 本地（Git Bash，frontend/ 下）
npm test                             # 前端回归（vitest run，BF↔测试一致性用）
```

AI 协作顺序即 CLAUDE.md"验证命令"的顺序：静态检查 → 冒烟 → 回归 → 全量。
AI 可以生成回归用例，但断言与边界必须对照 BF 档案的复现数据人工核对后才算数。

## 6. BF 归档闭环规则（新增）

在 CLAUDE.md「Bug 修复归档」既有规则之上追加一条硬约束：

> **重要 Bug 修复必须附带回归测试**：`@pytest.mark.regression` 标记、docstring 注明
> BF 编号，归档"影响范围与注意事项"写明测试节点 ID。CI 的一致性检查不通过即红。

模板同步更新见 `docs/bugfix/README.md`。BF-001~005 按 §2 基线表本次补齐（BF-002/003
为当时漏测，属于追溯补账，不改变已验证的修复本身）。

### 6.1 回归测试退役规则（何时允许移除）

回归测试是复发信号的钉子，而当前全量 <10s、成本可忽略，因此**默认永不退役**；
退役是例外动作，只允许走以下白名单之一，并留下与新增同等的追溯痕迹。

**可退役条件（满足其一）**

1. **契约废弃**：被测的外部契约（接口版本、参数口径、配额/并发上限）已被官方
   废弃，断言对象不复存在（例：假设有钉住 v2 place 参数的用例，v2 下线后即符合
   本条）。前提：接替的新契约已有自己的回归测试；
2. **路径删除**：被测代码路径被整体移除（重写导致断言永不执行）。

**不构成退役**（走「修订」而非退役）：

- 数值随契约调整（如官方放宽并发上限）→ 修订断言并同步 docs/02（镜像口径，
  见 `tests/unit/test_config.py` 的默认值断言说明）；
- 断言与根因不符的假回归（BF 档案本身有误）→ 修订断言 + 档案追加勘误。

**退役动作（与引发改动同一 PR）**

1. 索引表该行状态列改 `已退役（YYYY-MM-DD）`——**严禁删除索引行**（删行即绕过
   门禁，review 拦下）；
2. BF 档案末尾追加「退役记录」段（模板见 `docs/bugfix/README.md`）：日期、命中
   条款、证据（官方公告链接 / 删除 diff 提交）、替代防线、退役提交 hash；
3. 测试移除 `@pytest.mark.regression` 标记；用例若仍有一般价值可降级为无标记
   普通用例，docstring 编号改写为「原 BF-XXX（已退役）」或一并删除（两种写法
   脚本均放行）；
4. PR 描述写明条款与替代防线，走 ≥1 human review。

**门禁行为**：一致性检查对状态含「已退役」的条目免于双向校验——无测试引用不
红灯、残留引用不算孤儿，映射表标注「（已退役，免检）」。脚本无法判断退役是否
正当：把现役条目改标已退役以逃避补测试同样能过脚本，这一关由 review 对照档案
「退役记录」把守（人工复核点）。

## 7. branch protection（人工配置项，yml 做不到）

> ✅ **已于 2026-10-07 配置生效**（仓库设置侧完成，含 required checks 与 ≥1 review）。
> 以下步骤保留作重配 / 仓库迁移参考。

CI workflow 只能让 PR 显示红叉；**required checks + 合并限制**必须配在仓库设置里：

1. Settings → Branches → Add branch ruleset（或经典 branch protection rule）；
2. 目标分支 `main`；
3. 勾选 *Require a pull request before merging*，approvals ≥ 1；
4. 勾选 *Require status checks to pass*，选中两个 required check：
   `后端 lint / typecheck / test`、`前端 lint / build`（即 job 的 name 字段）；
5. 勾选 *Require branches to be up to date*（可选，避免合并漂移）。

验收方式：故意推一个测试不过的 PR，Merge 按钮必须不可用。

## 8. 验收标准

| # | 标准 | 验证方法 |
|---|---|---|
| 1 | `pytest -m smoke` / `-m regression` 可独立运行，marker 注册无 warning | 本地实跑 |
| 2 | 把 `poi_max_pages` 默认改回 5（BF-005 缺陷复现），`pytest -m regression` 变红 | 破坏性验证后还原 |
| 3 | 域层覆盖率 < 95 时 CI 失败，覆盖率写入 Job Summary | `--cov-fail-under=95` + summary 步骤 |
| 4 | 删除任一 BF 编号在 tests/ 的引用，一致性检查退出码非 0 并指出缺口 | 破坏性验证后还原 |
| 5 | branch protection 生效：红 CI 的 PR 无法合并 | ✅ 已配置（2026-10-07） |
| 6 | 退役语义：已退役条目无引用不红灯、残留引用不算孤儿；现役规则不变 | ✅ 已实测（临时改标 BF-002/003 + 删 BF-002 引用，放行后还原，§6.1） |
