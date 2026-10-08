"""连续失败计数熔断器（docs/02 §3.4）。

状态机：CLOSED →（连续失败 ≥ 阈值）→ OPEN →（open_s 到期）→ HALF_OPEN
→（探针成功 → CLOSED / 探针失败 → 重新 OPEN）。
纯逻辑 + 可注入假时钟（与 ratelimit.TokenBucket 同模式）；方法内无 await，
事件循环单线程内天然原子。只统计瞬时错误（调用方按 ErrorKind 过滤），
AUTH/QUOTA 等配置类错误不触发熔断——换请求也是同样结果，禁通道只会掩盖根因。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "CLOSED"  # 正常放行
    OPEN = "OPEN"  # 快速失败（不出站、不耗配额）
    HALF_OPEN = "HALF_OPEN"  # 到期试探：只放一个探针


class CircuitBreaker:
    def __init__(
        self, fail_threshold: int, open_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if fail_threshold < 1 or open_s <= 0:
            raise ValueError("fail_threshold 须 ≥ 1 且 open_s 须为正")
        self._fail_threshold = fail_threshold
        self._open_s = open_s
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probing = False

    @property
    def state(self) -> BreakerState:
        """当前状态。OPEN → HALF_OPEN 的迁移在 allow() 内按到期时间惰性触发。"""
        return self._state

    def allow(self) -> bool:
        """是否放行本次出站请求。OPEN 未到期 → False；HALF_OPEN 仅放一个探针。"""
        if self._state is BreakerState.CLOSED:
            return True
        if self._state is BreakerState.OPEN and self._clock() - self._opened_at >= self._open_s:
            self._state = BreakerState.HALF_OPEN
            self._probing = False
        if self._state is BreakerState.HALF_OPEN:
            if self._probing:
                return False
            self._probing = True
            return True
        return False  # OPEN 未到期

    def record_success(self) -> None:
        """任一成功即闭合：通道是否恢复只需一个成功样本判定。"""
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._probing = False

    def record_failure(self) -> None:
        """记录一次瞬时失败；半开探针失败立即重回 OPEN。"""
        self._probing = False
        if self._state is BreakerState.HALF_OPEN:
            self._trip()
            return
        self._failures += 1
        if self._failures >= self._fail_threshold:
            self._trip()

    def abandon_probe(self) -> None:
        """探针调用未决（如任务被取消）时释放半开名额，避免永久卡死在半开态。"""
        self._probing = False

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._failures = 0
