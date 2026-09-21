"""令牌桶单测：假时钟确定性验证突发、限速与补充逻辑。"""

import pytest

from app.core.ratelimit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_burst_then_throttle() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=5.0, capacity=10.0, clock=clock)
    waits = [bucket.take() for _ in range(12)]
    # 容量 10：前 10 次立即通过
    assert waits[:10] == [0.0] * 10
    # 第 11 次缺 1 个令牌 → 等 1/5=0.2s；第 12 次缺 2 个 → 0.4s
    assert abs(waits[10] - 0.2) < 1e-9
    assert abs(waits[11] - 0.4) < 1e-9


def test_tokens_refill_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=5.0, capacity=10.0, clock=clock)
    for _ in range(10):
        bucket.take()
    clock.now += 1.0  # 补充 5 个令牌
    assert bucket.take() == 0.0


def test_invalid_args_rejected() -> None:
    with pytest.raises(ValueError, match="必须为正"):
        TokenBucket(rate=0, capacity=10)
    with pytest.raises(ValueError, match="必须为正"):
        TokenBucket(rate=5, capacity=-1)
