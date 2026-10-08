# OpenMap · 15 分钟生活圈智能体检与规划助手

基于百度地图开放能力的社区"体检"工具：输入一个中心点，自动计算**真实路网**约束下的
15 分钟步行等时圈，统计四类民生设施（医疗/教育/购物/养老）覆盖情况，输出可视化体检报告，
并标注 1 公里内缺少菜市场/药店/小学的**服务盲区**。

> 参赛命题：【开源 AI 工具赛道】基于地图开放能力的"15 分钟生活圈"智能体检与规划助手
> 需求分析见 [docs/01-需求分析报告.md](docs/01-需求分析报告.md)，技术方案见 [docs/02-技术方案设计.md](docs/02-技术方案设计.md)
> 交付文档：[API 调用策略](docs/03-地图API调用策略.md) · [等时圈算法](docs/04-等时圈生成算法设计.md) · [POI 清洗策略](docs/05-多源POI数据清洗策略.md) · [盲区识别算法](docs/06-服务盲区识别算法.md) · [真实对比测试报告](docs/07-真实社区对比测试报告.md)

## 功能特性

| 状态 | 功能 |
|------|------|
| ✅ M1 | 地址搜索定位（地理编码）、地图点选中心点、分类 POI 检索与展示 |
| ✅ M2 | 基于真实路网的 15 分钟步行等时圈（扇形采样 + 批量矩阵 + 并行二分） |
| ✅ M3 | 覆盖统计（三级漏斗）、盲区识别、体检报告三件套（雷达图/柱状图/热力图）+ 图片/PDF/JSON 导出 |
| ✅ M4 | 限流容错降级链硬化（熔断/三级降级/QuotaBudget）、等时圈双法交叉校验与加密、API 预算控制（单次分析 ≤ 25 次调用） |
| ✅ M5 | 技术文档四篇（docs/03~06）、真实社区对比测试报告（docs/07，交道口 vs 回龙观实测）、固化示例数据（data/samples/） |

## 技术栈

- **后端**：Python 3.12 · FastAPI · httpx · pydantic v2 · Redis（可选，未配置自动退化内存缓存）· structlog
- **前端**：React 18 · TypeScript · Vite · Ant Design 5 · ECharts 5 · 百度地图 GL
- **部署**：Docker Compose（nginx + api + redis 三容器）
- **工程**：GitHub Actions（lint / typecheck / test / build）· ruff + mypy · ESLint + tsc · Apache-2.0

## 快速开始

### 方式一：Docker 一键启动（评审推荐）

```bash
cp .env.example .env      # 填入你的 API Key；或保持 DEMO_MODE=1 零配额体验
docker compose up -d
# 打开 http://localhost
```

`OPENMAP_DEMO_MODE=1` 时后端切换为**快照回放模式**，不消耗任何百度 API 配额，
内置示例社区（北京市海淀区中关村街道）可直接演示。

### 方式二：本地开发

**一键启动/重启（Windows 推荐）**：双击仓库根目录 `start-dev.bat`——自动停旧起新，后端（WSL, :8000）与前端（Vite, :5173）在两个独立窗口显示日志，关闭窗口即停止服务。

**手动启动：**

```bash
# 后端（Windows 用户请在 WSL 中执行；Windows 侧无可用 Python）
cd backend
python3 -m pip install --break-system-packages -e ".[dev]"
uvicorn app.main:app --reload --port 8000    # 未配置 AK 时自动回放模式启动

# 前端（另开终端）
cd frontend
npm install
npm run dev               # http://localhost:5173，/api 自动代理到 8000
```

### 常用命令（Makefile）

```bash
make dev-fe        # 前端开发服务
make test          # 后端 + 前端全部测试
make lint          # ruff + mypy + eslint + tsc
make demo          # 以 DEMO_MODE 启动后端
make compose-up    # Docker 全栈
```

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `OPENMAP_BAIDU_AK` | 演示模式外必填 | 百度地图 Web 服务 AK（服务端，仅存后端） |
| `OPENMAP_BAIDU_SK` | 否 | SN 签名密钥（校验方式选 SN 时填写） |
| `OPENMAP_DEMO_MODE` | 否 | `1` = 快照回放模式，零 API 消耗 |
| `OPENMAP_REDIS_URL` | 否 | 空 = 进程内缓存；compose 内置 `redis://redis:6379/0` |
| `OPENMAP_API_QPS` | 否 | 令牌桶速率，默认 5（按账号配额调整） |
| `VITE_BMAP_AK` | 演示模式外必填 | 百度地图 JS API AK（浏览器端，需配置域名白名单） |

完整项见 [.env.example](.env.example)。**密钥永不提交仓库**（已配 gitleaks 扫描）。

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查（含 Provider/缓存模式） |
| GET | `/api/v1/geocode?q=&city=` | 地址 → BD09 坐标候选列表 |
| GET | `/api/v1/pois?lng=&lat=&crs=bd09&categories=medical,education` | 分类 POI 检索（圆形区域 + 翻页聚合 + 去重清洗） |
| POST | `/api/v1/isochrone` | 多级步行等时圈（v1 射线采样 + 批量矩阵 + 闭合样条，`{origin, levels_min?}`） |
| GET | `/api/v1/demo/hint` | 演示模式示例（示例地址 + 中心点 + 类别） |

## 项目结构

```
├── backend/app/
│   ├── core/        # 配置、坐标系转换(BD09 规范)、限流、缓存、日志
│   ├── mapapi/      # MapProvider 端口 + 百度适配器 + 快照回放适配器（唯一出站点）
│   ├── poi/         # POI 分类检索、去重清洗管道
│   ├── api/         # FastAPI 路由（薄层）
│   └── models/      # pydantic 领域模型
├── frontend/src/
│   ├── components/map/    # 地图画布 + 图层（OriginMarker / PoiMarkers / ...）
│   ├── pages/             # AnalysisPage 主工作台
│   ├── stores/            # zustand 状态
│   └── api/               # axios + SSE 封装
├── data/replays/    # 演示/测试用 API 快照（回放模式数据源）
├── data/samples/    # 真实社区固化数据集（docs/07 对比报告素材，collect_samples.py 采集）
├── docs/            # 需求分析、技术方案、交付文档（03~07）与测试报告
│   └── bugfix/      # 重要 Bug 修复归档（现象→根因→修复→验证，规则见 CLAUDE.md）
└── docker-compose.yml
```

## 测试与质量

```bash
cd backend && pytest -q          # 单测(坐标/限流) + 集成(respx mock 异常矩阵) + 回放快照
cd frontend && npm run build     # tsc --strict + vite build
```

CI（GitHub Actions）：PR 触发 lint → test → build 三阶段，coverage gate 70%。

## 许可证

[Apache-2.0](LICENSE)
