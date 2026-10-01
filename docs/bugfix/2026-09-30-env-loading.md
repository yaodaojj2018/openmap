# BF-002 根目录 .env 双端均不生效（后端 cwd / vite envDir / 启动脚本硬编码）

- 日期：2026-09-30 　提交：a377f8f 　影响模块：`core/config` `frontend/vite.config` `start-dev.bat`

## 现象

用户按文档将两个 AK（服务端 `OPENMAP_BAIDU_AK`、浏览器端 `VITE_BMAP_AK`）填入**仓库根 `.env`** 后：
后端健康检查仍报 `provider: replay` 且无 AK 生效迹象；前端 `import.meta.env.VITE_BMAP_AK` 为 undefined。

## 根因分析

三处配置读取路径都没指向根 `.env`，逐一排查：

1. **后端**：`SettingsConfigDict(env_file=".env")` 按**进程 cwd** 解析。`start-dev.bat` 在 `backend/` 下启动
   uvicorn → 实际读 `backend/.env`（不存在）→ 全部落默认值
2. **前端**：Vite 默认 `envDir = 项目根`（即 `frontend/`）→ 根目录的 `VITE_BMAP_AK` 根本不进 bundle
3. **启动脚本**：`start-dev.bat` 的 uvicorn 命令行硬编码 `OPENMAP_DEMO_MODE=1`，即使 `.env` 写 0 也会被覆盖

## 修复方案

- `core/config.py`：`env_file` 改绝对路径 `REPO_ROOT / ".env"`（`_find_repo_root()` 已有探测逻辑，复用），
  任意 cwd 启动均读同一份
- `frontend/vite.config.ts`：`envDir: '..'` 指向仓库根，前后端共享一份 `.env`
- `start-dev.bat`：删掉命令行 `OPENMAP_DEMO_MODE=1`，模式完全由 `.env` 决定（compose 的默认值不受影响）

## 验证结果

- 后端：`Settings()` 从任意目录实例化，`effective_summary()` 报 `ak_configured=true, sn_signing` 与 `.env` 一致；
  健康检查 `provider: baidu`（demo=0 时）
- 前端：`npm run build` 产物中可检索到 `VITE_BMAP_AK` 注入值；地图底图正常加载

## 影响范围与注意事项

- 密钥文件布局从此固定为"根目录一份 `.env`"，README/.env.example 已同步
- **复发信号**：改了 `.env` 但行为不变（八成又是路径/优先级问题）；排查口诀——
  后端看启动日志 `effective_summary`，前端看 bundle 里的 env 注入
