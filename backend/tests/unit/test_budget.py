"""QuotaBudget 单测：作用域隔离、计数口径与超限护栏（docs/02 §3.4）。"""

import asyncio

import pytest

from app.core.budget import BudgetExceeded, budget_scope, current_budget


def test_scope_sets_and_resets() -> None:
    assert current_budget() is None
    with budget_scope(5) as budget:
        assert current_budget() is budget
        budget.acquire()
        assert budget.http_calls == 1
    assert current_budget() is None


def test_acquire_blocks_at_limit_without_overcount() -> None:
    with budget_scope(2) as budget:
        budget.acquire()
        budget.acquire()
        with pytest.raises(BudgetExceeded) as exc_info:
            budget.acquire()
        assert exc_info.value.max_calls == 2
        assert exc_info.value.used == 2
        assert budget.http_calls == 2  # 被拦截的尝试不重复计数
        assert budget.remaining == 0


async def test_gather_children_share_budget() -> None:
    """asyncio.gather 子任务继承 context：共享同一预算实例，计数聚合。"""

    async def consume() -> None:
        for _ in range(3):
            budget = current_budget()
            assert budget is not None
            budget.acquire()

    with budget_scope(6) as budget:
        await asyncio.gather(consume(), consume())
        assert budget.http_calls == 6


def test_cache_hit_and_retry_counters() -> None:
    with budget_scope(10) as budget:
        budget.record_cache_hit()
        budget.record_retry()
        budget.record_retry()
        assert (budget.cache_hits, budget.retries) == (1, 2)
        assert budget.http_calls == 0  # 命中/调度本身不占出站名额


def test_invalid_limit_rejected() -> None:
    with pytest.raises(ValueError, match="须"), budget_scope(0):
        pass
