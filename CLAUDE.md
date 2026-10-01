# CLAUDE.md — 本项目 AI 协作规则

所有 AI 助手（Claude / Copilot 等）在本仓库工作前**必须完整阅读本文件**。

## 项目背景

参赛项目：基于百度地图开放能力的"15 分钟生活圈"智能体检与规划助手。
两份纲领文档是唯一事实来源，任何实现决策先对照它们：

1. `docs/01-需求分析报告.md` — 需求、评分权重、交付清单（§8）、开放问题（§9）
2. `docs/02-技术方案设计.md` — 选型、架构、算法、模块划分、路线图（§10）

**改架构必须先改文档，再改代码。** 路线图分 M1~M5，做哪个里程碑先读对应章节。

## 环境事实（Windows 宿主，勿踩坑）

| 事实 | 影响 |
|------|------|
| Windows 侧 `python` 是 Microsoft Store **桩程序**，不可用 | 后端一律在 **WSL** 执行：`wsl -e bash -c "..."`，路径 `/mnt/d/DockerWSL/openmap` |
| WSL Ubuntu 3.12.3，但**缺 python3-venv**（`python3 -m venv` 生成的目录无 pip） | 后端依赖用系统 pip 安装：`python3 -m pip install --break-system-packages -e ".[dev]"`（已装好）；**不要**尝试 venv |
| WSL 调用输出含 UTF-16 告警头，管道过滤要 `tr -d '\0'` | 验证命令输出勿用 grep 吞错误（曾导致误报"install ok"） |
| Windows 侧 node v24 / npm 可用 | 前端命令直接在 Git Bash 执行 |
| `make` 仅存在于 WSL（Git Bash 无 make） | 后端相关 make 目标在 WSL 执行；前端在 Git Bash 直跑 npm；一键启动/重启用根目录 `start-dev.bat`（双击即可） |
| Docker Desktop 可用（WSL2 后端） | compose 构建验证可用，但较慢，优先本地验证 |
| 本目录不是 git 仓库时要先 `git init` | 交付要求 Git 托管，Apache-2.0 |

## 架构铁律（violations = 必须拒绝的 PR）

1. **依赖单向**：`api/ → tasks/ → 域层(isochrone|poi|coverage|blindspot) → mapapi/ → core/`。
   域层之间禁止横向 import；下层禁止 import 上层。
2. **域层纯函数**：`isochrone/ poi/ coverage/ blindspot/` 内禁止任何 IO（网络/Redis/文件），
   只允许"坐标/几何进，几何/数值出"。需要外部数据时由 pipeline 编排注入。
3. **mapapi/ 是唯一出站 HTTP 点**：限流/重试/熔断/缓存/SN 签名全部收口在适配器内，
   域层与路由层对故障细节无感。新增地图能力 = 先扩展 `provider.py` 协议。
4. **外部约束全配置化**：配额、批量上限、状态码分类、类目映射 → `core/config.py` + yaml，
   禁止散落硬编码。百度 API 的批量上限/QPS 以当期官方文档为准，做成可配。
5. **内部坐标系 BD09**：所有内部计算与 API 响应统一 BD09；入口处用 `core/coords.py`
   转 WGS84/GCJ02。对外接口坐标字段必须带 `crs` 声明。
6. **密钥纪律**：`OPENMAP_BAIDU_AK/SK` 只存后端环境变量，永不出现在前端代码、日志、
   git 历史中。前端用独立的 `VITE_BMAP_AK`（域名白名单）。日志输出配置摘要必须脱敏。
7. **API 预算意识**：任何新功能先估算调用次数（对照 docs/02 §3.1.3 预算表），
   单次分析硬上限 40 次；能用批量矩阵不逐条，能缓存不重发，能本地算（haversine/cKDTree）不调 API。

## 代码规范

- 后端：ruff（lint + format，line-length 100）、mypy、pytest（asyncio_mode=auto）。
  docstring 用中文，写清楚"为什么"；算法函数注释给出公式与误差界。
- 前端：ESLint 9 flat config + Prettier + `tsc --strict`；组件单一职责；
  地图 Overlay 通过 MapContext 组合，图层组件化（M2 的 IsochroneLayer 按此扩展）。
- 提交：Conventional Commits（`feat: ...` / `fix: ...` / `docs: ...`）。

## 验证命令（改动后必须跑）

```bash
# 后端（WSL 内，位于 backend/，系统 pip 已装依赖）
python3 -m ruff format --check . && python3 -m ruff check . && python3 -m mypy && python3 -m pytest -q

# 前端（Windows Git Bash，位于 frontend/）
npm run lint && npm run build

# compose 配置合法性（仓库根）
docker compose config -q
```

任何"完成"的声明必须附带上述验证的实际输出结果；测试失败就明说，禁止跳过或注释掉测试。

## Bug 修复归档

**重要 Bug 修复完成并验证后，必须同提交写入 `docs/bugfix/` 归档**（模板与索引见
`docs/bugfix/README.md`）：

- "重要"的判定：影响功能正确性 / 数据准确性 / 触发外部告警，且需要根因分析才能修的；
  样式调整、文案 typo、开发环境踩坑不算（环境踩坑记入上文"环境事实"表，不重复建档）。
- 文件名 `YYYY-MM-DD-<slug>.md`（日期 = 修复提交当日），内容五段：现象 → 根因分析（含定位过程）→
  修复方案 → 验证结果（贴实际输出，禁止无输出的"已验证"）→ 影响范围与复发信号。
- 新条目同步登记到 `docs/bugfix/README.md` 索引表（含修复提交 hash）。

## 演示模式（评审保底）

`OPENMAP_DEMO_MODE=1` → `mapapi/replay/` 回放 `data/replays/demo.json` 快照，
全链路零配额。**凡是新增 Provider 方法，必须同步扩展回放实现与快照数据**，
并在 `tests/` 用回放数据断言行为，否则 CI 不许过。

## 文档与交付对照

- 评分维度 → 技术对策映射表（docs/01 §7 / docs/02 附录）：改功能时自查是否破坏对应评分点。
- 交付清单（docs/01 §8）：LICENSE、一键部署、示例数据、四篇技术文档、真实对比测试报告、CI。
- 新增设计决策若与本文件冲突，先更新 `docs/02` 并在 PR 中说明理由。

## 强制规则

不知道的就问，没要求的不写，只改被要求的部分，给验收标准别给步骤