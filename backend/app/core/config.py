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
