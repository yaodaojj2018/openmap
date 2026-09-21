"""令牌桶限流（每 API Key 独立实例）。

纯逻辑（take）与异步等待（acquire）分离：take 可用假时钟确定性单测。
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float, capacity: float, clock=time.monotonic) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate 与 capacity 必须为正")
        self.rate = rate
        self.capacity = capacity
        self._clock = clock
        self._tokens = capacity
        self._last = clock()

    def take(self, amount: float = 1.0) -> float:
        """同步取令牌，返回需等待秒数（0 = 立即通过）。

        不足时预扣为负（记账需要等待期补足），调用方应 sleep(wait) 后视为成功。
        """
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        if self._tokens >= amount:
            self._tokens -= amount
            return 0.0
        deficit = amount - self._tokens
        self._tokens = -deficit
        return deficit / self.rate

    async def acquire(self, amount: float = 1.0) -> None:
        """异步获取令牌（排队不丢弃）。"""
        wait = self.take(amount)
        if wait > 0:
            await asyncio.sleep(wait)


class NullTokenBucket:
    """不限流占位（回放模式使用）。"""

    async def acquire(self, amount: float = 1.0) -> None:  # pragma: no cover
        return None
