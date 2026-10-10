# BF-011 真实模式下体检分析「卡在盲区识别」不结束（前端任务状态机缺终态迁移）

- 日期：2026-10-10 　提交：bd26a2b 　影响模块：`frontend/src/hooks/useAnalysisTask.ts`、`frontend/src/stores/analysis.ts`

## 现象

用户实测（杭州市和宁文华府，中心点 120.110885,30.342059）：选择设施类目后点击
「开始体检分析」，进度条一直停在「盲区识别」，按钮持续 loading，任务永不终止，
报告不弹出。切到真实百度 AK 后必现。

## 根因分析

分三层建立反馈环定位（后端 → 传输 → 前端），逐层排除：

1. **后端 pipeline 正常**。curl 直连 `POST :8000/api/v1/analyses` 创建同中心点新参数任务，
   6 秒内 `pending → completed`（含 blindspot 阶段）；`compute_blindspot` 是纯本地
   几何计算（15×15 栅格 + cKDTree，零 API），毫秒级，不可能卡。
2. **SSE 传输链路正常**。curl 走 Vite 代理（`:5173/api/...`）订阅 `/events`，
   `resolving → sampling → fitting → poi → coverage → blindspot → completed`
   全事件按时到达（约 12 秒，终态帧带 `status:"completed"`）。排除反向代理缓冲/断流。
3. **前端状态机缺陷**。审查 `useAnalysisTask` 的 `completed` 分支：只调用 `applyReport`
   （落等时圈/POI/报告），**全前端代码没有任何一处把 `taskStatus` 置为 `'completed'`**
   （grep 佐证：`taskStatus` 的赋值仅出现在 `taskStarted`/`taskStageChanged`/`taskFailed`）。
   而 `AnalysisPage` 的 `taskRunning = taskStatus ∈ {pending, running}` 与进度条显示的
   `taskStage` 因此永久停留在最后上报值——`blindspot` / `running`。

`git log` 溯源：该缺陷自 `d030108`（M3 分析任务化）起即存在，非回归。此前未暴露的原因：
演示模式回放极快，`createAnalysis` 返回时任务已是 `completed`，前端走「参数复用」路径
（`taskStarted` 直接带入 `completed` 状态），绕开了 SSE 完成分支；切换真实 AK 后任务需
十余秒，`POST` 返回时是 `running`，遂走 SSE 路径命中缺陷。

## 修复方案

- `frontend/src/stores/analysis.ts`：新增终态 action `taskCompleted()`——
  `taskStatus:'completed'`、`taskStage:'completed'`、`taskProgress:1`，补齐状态机缺失的
  终态迁移（此前只有 `taskFailed`/`cancelled` 侧有终态）。
- `frontend/src/hooks/useAnalysisTask.ts`：`applyReport` 末尾调用 `s.taskCompleted()`。
  `applyReport` 是两条完成路径（SSE 事件取报告 / 参数复用秒回）的公共收口点，在报告
  落档处一并终态化，避免两处各写一遍、日后再次漂移。
- CI 基建（纯前端缺陷的回归 seam 在前端，后端 pytest 测不到）：
  - 引入 vitest（`frontend/vitest.config.ts` + `npm test`），新增
    `frontend/tests/analysis-task.test.tsx` 回归用例；
  - `scripts/check_bugfix_regression.py` 的引用来源由 `backend/tests` 扩为
    `backend/tests` **或** `frontend/tests`，避免为过门禁在后端伪造弱 seam；
  - `ci.yml` 前端 job 加 `npm test`；`docs/regression-ci.md` §3/§5 与 CLAUDE.md 验证命令同步。

## 验证结果

修复后全量验证（WSL backend/ + Git Bash frontend/）：

```text
# 后端（未改动，回归确认）
$ python3 -m ruff format --check . && python3 -m ruff check . && python3 -m mypy && python3 -m pytest -q
Success: no issues found in 50 source files
152 passed, 2 warnings in 34.56s

# 前端
$ npm run lint && npm test && npm run build
> eslint .            # 无输出 = 通过
 ✓ tests/analysis-task.test.tsx (1 test) 115ms
 Test Files  1 passed (1)
      Tests  1 passed (1)
✓ built in 1m 54s

# BF 归档 ↔ 回归测试一致性
$ python3 scripts/check_bugfix_regression.py
BF 归档 ↔ 回归测试映射（索引 11 项，已退役 0 项）：
  ...
  BF-011: frontend/tests/analysis-task.test.tsx
OK：BF 归档与回归测试双向一致。
```

**回归测试有效性双向佐证**（临时注释掉修复行 `s.taskCompleted()` 后重跑，确认测试确能捕获缺陷）：

```text
× BF-011：SSE completed 事件后任务进入终态，不停留在 running/blindspot
  → expected 'running' to be 'completed' // Object.is equality
 Test Files  1 failed (1)   Tests  1 failed (1)
```

恢复修复行后同一用例转为 `1 passed`——失败信息与用户症状（`taskStatus` 停留 `running`）
逐字对应。

**端到端状态机复核**（Node 24 内置 EventSource 复刻前端 hook 事件处理，真实后端 SSE，
中心点 120.110885,30.342059，验证后删除脚本）：

```text
created: 36260fbb89de pending pending
completed event at stage: blindspot      ← 精确复现用户卡住的位置
report fetched, blindspot types: 1
final taskStatus: completed | taskStage: completed
PASS: 任务终态化成功，taskRunning=false
```

## 影响范围与注意事项

- 波及：所有走 SSE 完成任务的真实模式分析（修复前按钮 loading 永不结束、报告区不展开）；
  演示模式参数复用路径实际曾被 `taskStarted` 的 completed 入参掩盖，修复后行为一致。
- 复发信号：点击「开始体检分析」后进度条停在最后阶段且按钮持续 loading；报告已可用却
  不展开。凡新增「完成任务」的入口，必须经 `applyReport`（或显式 `taskCompleted()`）落终态。
- 回归测试节点（`frontend/tests/analysis-task.test.tsx`，用例名含 `BF-011`）：
  `useAnalysisTask 任务状态机 › BF-011：SSE completed 事件后任务进入终态，不停留在 running/blindspot`；
  `scripts/check_bugfix_regression.py` 索引表 ↔ 测试引用双向校验覆盖该节点。
