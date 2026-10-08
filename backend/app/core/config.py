"""全局配置：所有外部约束集中于此，环境变量前缀 OPENMAP_。

配额/批量上限等以当期官方文档为准，均可通过环境变量覆盖（见 .env.example）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    """定位仓库根：从 cwd 与模块路径向上探测 data/replays/demo.json。

    兼容三种运行布局：仓库根开发运行 / backend/ 内运行 / 容器内 /app 运行。
    """
    marker = Path("data") / "replays" / "demo.json"
    for base in (Path.cwd(), *Path(__file__).resolve().parents):
        if (base / marker).exists():
            return base
    return Path(__file__).resolve().parents[3]


REPO_ROOT = _find_repo_root()


class Settings(BaseSettings):
    """系统配置。字段名与 env 一一对应，如 OPENMAP_BAIDU_AK。"""

    model_config = SettingsConfigDict(
        # 绝对路径指向仓库根 .env：uvicorn 无论从仓库根还是 backend/ 启动都能读到同一份
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="OPENMAP_",
        extra="ignore",
    )

    app_name: str = "openmap"
    app_version: str = "0.1.0"
    debug: bool = False

    # ---- 地图 API（百度 Web 服务 API）----
    baidu_ak: str = ""  # 服务端 AK；空且非 demo 模式时启动告警
    baidu_sk: str = ""  # 可选：SN 签名密钥（校验方式选 SN 时必填）
    demo_mode: bool = False  # true 时 Provider 切换为快照回放，零配额消耗
    replay_file: str = str(REPO_ROOT / "data" / "replays" / "demo.json")

    # ---- 网络与限流（默认对齐百度免费档"地点检索并发 3"的口径）----
    api_qps: float = 2.0  # 令牌桶速率：每秒补充令牌数（留余量压在并发 3 以下）
    api_burst: float = 3.0  # 令牌桶容量：允许的瞬时并发上限
    http_timeout_s: float = 8.0
    retry_max_attempts: int = 3  # 含首次，仅对可重试错误生效
    retry_backoff_s: float = 0.5  # 指数退避基数

    # ---- 熔断（docs/02 §3.4：连续瞬时失败 → 打开期内快速失败，不出站不耗配额）----
    breaker_fail_threshold: int = 5  # 连续瞬时错误（TIMEOUT/SERVER/RATE_LIMIT）达阈值 → 打开
    breaker_open_s: float = 30.0  # 打开时长；到期后半开放单个探针请求

    # ---- 缓存 ----
    redis_url: str = ""  # 空 = 进程内内存缓存（开发/CI 友好）
    cache_ttl_s: int = 7 * 24 * 3600  # POI/测时结果缓存 7 天

    # ---- POI 检索 ----
    # v3 地点检索分页契约（官方文档 /place/v3/around）：page_size 取值 10-20（默认 10、
    # 最大 20，越界被服务端静默钳制）；page_num 仅允许 0、1、2 —— 越界页返回 status=0
    # 空结果（已实测），既浪费配额又无报错，故在配置层强制校验而非静默容错。
    poi_page_size: int = Field(default=20, ge=10, le=20)  # 单页条数上限（v3 口径 20）
    poi_max_pages: int = Field(default=3, ge=1, le=3)  # v3 最多 3 页/查询 → 单类目召回上限 60
    poi_search_radius_m: int = 1300  # 覆盖 15min 步行量级（约 1.1km）外扩
    poi_dedup_distance_m: float = 30.0  # 距离阈值内去重
    poi_taxonomy_file: str = str(REPO_ROOT / "data" / "config" / "poi_taxonomy.json")

    # ---- 等时圈引擎（docs/02 §3.1）----
    matrix_batch_size: int = 50  # routematrix 单批目的地上限（官方口径，可配）
    isochrone_directions: int = 16  # 扇形采样方向数
    isochrone_rings_m: list[int] = [300, 600, 900, 1200]  # 粗采样距离档（米）
    isochrone_refine_rounds: int = 3  # 并行二分轮数（区间收敛至 ~50m）
    isochrone_levels_min: list[int] = [5, 10, 15]  # 输出的等时圈级别

    # ---- 等时圈双法交叉校验与加密（docs/02 §3.1 阶段 C/D，纯几何零额外基础配额）----
    isochrone_grid_step_m: int = 100  # 第二法网格步长（marching squares 格距）
    isochrone_grid_extent_m: int = 2400  # 网格覆盖半径；兼作散点外推环半径，保证级别圈闭合
    isochrone_verify_tolerance_m: float = 100.0  # 双法径向偏差阈值，超出即加密重采样
    isochrone_densify_max_probes: int = 10  # 单轮加密探针总上限（仍落在 1 次批量矩阵内）
    isochrone_enclave_jump_ratio: float = 2.0  # 相邻方向临界半径比阈值 → 疑似飞地扇区

    # ---- 覆盖判定三级漏斗（docs/02 §3.2）----
    coverage_edge_band_min: float = 2.0  # 边缘带半宽：t̂ ∈ [T±band] 触发矩阵精判
    coverage_matrix_verify_limit: int = 30  # 三级漏斗 API 上限：超出的设施按插值口径收尾
    coverage_sufficient_count: int = 5  # 类目"数量充足"阈值（评分公式，见 models/coverage.py）
    coverage_field_confidence: float = 0.8  # 时间场插值口径的置信度标注

    # ---- 盲区识别（docs/02 §3.3，全本地零 API）----
    blindspot_extent_m: int = 1500  # 研究区域边长（1.5km × 1.5km）
    blindspot_cell_m: int = 100  # 栅格分辨率（米）
    blindspot_threshold_m: int = 1000  # 距设施超此值判缺失（命题口径 1km）
    blindspot_buffer_m: float = 50.0  # 缺失栅格聚合平滑缓冲（8 邻接融合）
    blindspot_types: list[str] = ["medical", "education", "shopping"]  # 参与判定的关键设施类目

    # ---- 分析任务（docs/02 §5.2）----
    task_ttl_s: int = 24 * 3600  # 任务状态/报告持久化 TTL（Redis 场景跨重启可恢复）
    analysis_api_budget: int = 40  # 单次分析出站 HTTP 硬上限（docs/02 §3.4 QuotaBudget）

    # ---- 降级链第三级（docs/02 §3.4：矩阵/逐条均失败时直线×1.3 模型估算兜底）----
    fallback_walk_speed_mps: float = 1.35  # 模型步速 m/s（与回放模拟口径一致）
    fallback_detour_factor: float = 1.3  # 直线→路网经验系数
    fallback_confidence_cap: float = 0.3  # 估算模式的等时圈置信度封顶（低置信度标注）

    # ---- 百度 status 码分类覆盖表（键为 int 状态码字符串）----
    # 默认表见 mapapi/baidu/client.py；此处可增量覆盖，如 {"251": "RATE_LIMIT"}
    status_kind_overrides: dict[str, str] = {}

    # ---- CORS ----
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:80"]

    def effective_summary(self) -> dict[str, object]:
        """脱敏后的生效配置摘要（启动日志用，绝不输出密钥）。"""
        return {
            "demo_mode": self.demo_mode,
            "provider": "replay" if self.demo_mode or not self.baidu_ak else "baidu",
            "ak_configured": bool(self.baidu_ak),
            "sn_signing": bool(self.baidu_sk),
            "api_qps": self.api_qps,
            "cache": "redis" if self.redis_url else "memory",
            "poi_page_size": self.poi_page_size,
            "poi_max_pages": self.poi_max_pages,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
