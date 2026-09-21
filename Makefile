.PHONY: help install-be install-fe dev-be dev-fe test-be lint-be lint-fe build-fe lint test demo compose-up compose-down compose-config

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install-be: ## 安装后端依赖（WSL 内执行；本机无 python3-venv，用系统 pip）
	cd backend && python3 -m pip install --break-system-packages -e ".[dev]"

install-fe: ## 安装前端依赖
	cd frontend && npm install

dev-be: ## 启动后端开发服务（热重载，8000）
	cd backend && uvicorn app.main:app --reload --port 8000

dev-fe: ## 启动前端开发服务（5173，/api 代理到 8000）
	cd frontend && npm run dev

test-be: ## 后端测试
	cd backend && python3 -m pytest -q

lint-be: ## 后端 lint + 类型检查
	cd backend && python3 -m ruff format --check . && python3 -m ruff check . && python3 -m mypy

lint-fe: ## 前端 lint
	cd frontend && npm run lint

build-fe: ## 前端类型检查 + 构建
	cd frontend && npm run build

lint: lint-be lint-fe ## 全部 lint

test: test-be build-fe ## 全部验证（CLAUDE.md 验证命令）

demo: ## 演示模式启动后端（零 API 配额）
	cd backend && OPENMAP_DEMO_MODE=1 uvicorn app.main:app --port 8000

compose-config: ## 校验 compose 配置
	docker compose config -q && echo "compose config OK"

compose-up: ## Docker 全栈启动（http://localhost）
	docker compose up -d --build

compose-down: ## 停止并清理容器
	docker compose down
