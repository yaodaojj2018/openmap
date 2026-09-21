"""mapapi：地图能力端口-适配器层（全系统唯一出站 HTTP 点，CLAUDE.md 铁律 #3）。"""

from __future__ import annotations

from app.core.cache import CacheBackend
from app.core.config import Settings
from app.mapapi.provider import MapProvider


def build_provider(settings: Settings, cache: CacheBackend) -> MapProvider:
    """按配置装配 Provider：demo 模式 / 未配置 AK → 快照回放。"""
    if settings.demo_mode or not settings.baidu_ak:
        from app.mapapi.replay.client import ReplayProvider

        return ReplayProvider.from_file(settings.replay_file)
    from app.mapapi.baidu.client import BaiduClient

    return BaiduClient(settings, cache)
