"""熔断器单测：假时钟确定性验证状态机迁移（docs/02 §3.4）。"""

import pytest

from app.core.breaker import BreakerState, CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_breaker(threshold: int = 3, open_s: float = 30.0) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    return CircuitBreaker(threshold, open_s, clock), clock


def test_closed_allows_all() -> None:
    breaker, _ = make_breaker()
    assert breaker.state is BreakerState.CLOSED
    assert all(breaker.allow() for _ in range(10))


def test_trips_after_consecutive_failures() -> None:
    breaker, _ = make_breaker(threshold=3)
    for _ in range(2):
        breaker.record_failure()
    assert breaker.allow() is True  # 未达阈值仍放行
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False


def test_success_resets_consecutive_counter() -> None:
    breaker, _ = make_breaker(threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()  # 计数清零
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED  # 2 < 3 未打开


def test_open_blocks_until_expired_then_half_open_single_probe() -> None:
    breaker, clock = make_breaker(threshold=1, open_s=30.0)
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    clock.advance(29.9)
    assert breaker.allow() is False  # 未到期
    clock.advance(0.1)
    assert breaker.state is BreakerState.OPEN  # OPEN→HALF_OPEN 迁移惰性发生在 allow
    assert breaker.allow() is True  # 到期 → 半开探针
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow() is False  # 半开只放一个探针


def test_half_open_probe_success_closes() -> None:
    breaker, clock = make_breaker(threshold=1, open_s=30.0)
    breaker.record_failure()
    clock.advance(30.0)
    assert breaker.allow() is True
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True


def test_half_open_probe_failure_reopens() -> None:
    breaker, clock = make_breaker(threshold=1, open_s=30.0)
    breaker.record_failure()
    clock.advance(30.0)
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False
    clock.advance(30.0)  # 探针失败须再等一个完整 open_s
    assert breaker.allow() is True


def test_abandon_probe_releases_half_open_slot() -> None:
    """探针未决（调用方被取消）须可释放名额，否则熔断永久卡死在半开。"""
    breaker, clock = make_breaker(threshold=1, open_s=30.0)
    breaker.record_failure()
    clock.advance(30.0)
    assert breaker.allow() is True
    breaker.abandon_probe()  # 模拟取消：未 record_success/failure
    assert breaker.allow() is True  # 名额已释放，可再探


def test_invalid_args_rejected() -> None:
    with pytest.raises(ValueError, match="须"):
        CircuitBreaker(0, 30.0)
    with pytest.raises(ValueError, match="须"):
        CircuitBreaker(3, 0.0)
