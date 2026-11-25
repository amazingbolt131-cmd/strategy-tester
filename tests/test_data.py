"""Resample, as-of alignment, and the 17:00 New York bias lock."""

from datetime import datetime, timezone

import numpy as np

from backtest.data import Bars, align_asof, resample
from backtest.strategies import lock_bias_at_ny


def test_resample_builds_complete_buckets_only():
    # Five 1m bars starting on a 5-minute boundary, plus a stray sixth.
    start = 1_700_000_000_000
    start -= start % 300_000
    times = start + np.arange(6) * 60_000
    bars = Bars(
        open_time=times.astype(np.int64),
        open=np.arange(6, dtype=float) + 10,
        high=np.arange(6, dtype=float) + 12,
        low=np.arange(6, dtype=float) + 9,
        close=np.arange(6, dtype=float) + 11,
        volume=np.ones(6),
    )
    out = resample(bars, 5)
    assert len(out) == 1
    assert out.open[0] == 10
    assert out.close[0] == 15  # bar index 4, close = 4 + 11
    assert out.high[0] == 16  # index 4 high = 4 + 12
    assert out.low[0] == 9
    assert out.volume[0] == 5


def test_align_asof_uses_the_last_published_value():
    published = np.array([10, 20, 40])
    values = np.array([1, 2, 3])
    target = np.array([0, 10, 15, 20, 39, 40, 50])
    assert list(align_asof(published, values, target)) == [0, 1, 1, 2, 2, 3, 3]


def test_turtle_bias_locks_at_1700_new_york():
    # 24 Nov 2026 is EST (UTC-5). 17:00 NY is 22:00 UTC.
    # A 30m bar opening 21:30 UTC closes exactly at 17:00 NY.
    close_at = datetime(2026, 11, 24, 22, 0, tzinfo=timezone.utc)
    open_ms = int(close_at.timestamp() * 1000) - 30 * 60_000
    earlier = open_ms - 30 * 60_000
    opens = np.array([earlier, open_ms], dtype=np.int64)
    bias = np.array([1, -1])
    locked = lock_bias_at_ny(opens, bias, 30, 17)
    assert locked[0] == 0
    assert locked[1] == -1
    # The following bar keeps the snapshot.
    later = np.array([earlier, open_ms, open_ms + 30 * 60_000], dtype=np.int64)
    locked = lock_bias_at_ny(later, np.array([1, -1, 1]), 30, 17)
    assert list(locked) == [0, -1, -1]
