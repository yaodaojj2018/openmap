"""单次分析 API 预算护栏（docs/02 §3.4 QuotaBudget + §7.5 物理口径统计）。

计数口径 = 物理出站 HTTP（含批量分片与重试；缓存命中/熔断快速失败不计）。
通过 contextvar 按分析任务隔离：asyncio.gather 派生的子任务继承当前 context，
共享同一个 QuotaBudget 实例，计数天然聚合；Provider 协议签名零侵入。
core 层不感知 MapApiError（依赖单向：mapapi → core），超限抛 BudgetExceeded，
由适配器转译为 MapApiError(BUDGET_EXCEEDED) 后交降级链接管。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class BudgetExceeded(Exception):
    """出站次数达到 max_calls 上限，调用方不得再发起出站请求。"""

    def __init__(self, max_calls: int, used: int) -> None:
        super().__init__(f"出站预算耗尽：已用 {used}/{max_calls}")
        self.max_calls = max_calls
        self.used = used


class QuotaBudget:
    """单次分析的出站计数器 + 护栏。方法内无 await，事件循环内原子。"""

    def __init__(self, max_calls: int) -> None:
        if max_calls < 1:
            raise ValueError("max_calls 须 ≥ 1")
        self.max_calls = max_calls
        self.http_calls = 0  # 实际出站 HTTP 次数（含批量分片与重试）
        self.cache_hits = 0  # 缓存命中次数（零出站、零配额）
        self.retries = 0  # 可重试失败触发的重试调度次数

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.http_calls)

    def acquire(self) -> None:
        """出站前预检：预算内放行并计数；超限抛 BudgetExceeded（不再出站）。"""
        if self.http_calls >= self.max_calls:
            raise BudgetExceeded(self.max_calls, self.http_calls)
        self.http_calls += 1

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    def record_retry(self) -> None:
        self.retries += 1


_current: ContextVar[QuotaBudget | None] = ContextVar("openmap_api_budget", default=None)


def current_budget() -> QuotaBudget | None:
    """当前任务作用域内的预算；未设置（独立路由/回放模式）= 不限也不记。"""
    return _current.get()


@contextmanager
def budget_scope(max_calls: int) -> Iterator[QuotaBudget]:
    """为当前分析任务开启预算作用域，退出自动复位（context 随任务隔离）。"""
    budget = QuotaBudget(max_calls)
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)
