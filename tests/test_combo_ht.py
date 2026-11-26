"""HAMA 1h + Turtle 5m combo and the backtest period dropdown."""

from datetime import date

import numpy as np

from backtest import combo_ht
from backtest.config import (
    PERIODS,
    STRATEGIES,
    STRATEGY_BY_ID,
    TESTER_STRATEGIES,
    catalog,
    resolve_strategy,
    symbols_for,
    venue_enabled,
)
from backtest.data import Bars, period_window, test_window
from backtest.runner import _chart_tf
from backtest.sweep import combo_plan

DAY = 86_400_000


def test_combo_is_binance_only_and_only_on_the_single_run_tab():
    spec = STRATEGY_BY_ID["combo_ht_5m"]
    assert spec["label"] == "HAMA 1h + Turtle 5m Combo"
    assert venue_enabled(spec, "binance") and not venue_enabled(spec, "pivex")
    assert "combo_ht_5m" not in {s["id"] for s in STRATEGIES}
    assert TESTER_STRATEGIES[: len(STRATEGIES)] == STRATEGIES
    cat = catalog()
    assert cat["combo_ht_5m"]["venues"]["binance"]["enabled"]
    assert not cat["combo_ht_5m"]["venues"]["pivex"]["enabled"]
    assert symbols_for("combo_ht_5m", "binance")[:2] == ["BTCUSDT", "ETHUSDT"]
    assert resolve_strategy("combo_ht_5m", "binance")["label"] == spec["label"]
    canonical, _ = combo_plan()
    assert all(row["strategy_id"] != "combo_ht_5m" for row in canonical)


def test_period_windows():
    assert set(PERIODS) == {"1m", "3m", "6m", "1y"}
    day = date(2026, 11, 25)
    assert period_window("1m", day) == test_window(day)
    for key, days in (("3m", 91), ("6m", 182), ("1y", 365)):
        warm, start, end = period_window(key, day)
        assert end - start == days * DAY
        assert start - warm == 40 * DAY
        assert end == test_window(day)[2]


def test_chart_timeframe_coarsens_for_long_periods():
    assert _chart_tf("5m", 0, 30 * DAY) == "5m"
    assert _chart_tf("5m", 0, 365 * DAY) == "30m"
    assert _chart_tf("1h", 0, 365 * DAY) == "1h"


def _m1(prices):
    n = len(prices)
    t = np.arange(n, dtype=np.int64) * 60_000
    p = np.asarray(prices, dtype=float)
    return Bars(open_time=t, open=p.copy(), high=p.copy(), low=p.copy(), close=p.copy(), volume=np.ones(n))


def _sig(n5, atrd):
    return {
        "t": np.arange(n5, dtype=np.int64) * 300_000,
        "atrd": np.full(n5, atrd, dtype=float),
        "open5": np.full(n5, 100.0),
    }


def test_walk_half_off_at_3pct_then_breakeven():
    # Long from 100: +3% hits, then price falls back to entry: half at 103, half at BE.
    prices = [100.0] * 5 + [101.0, 103.0, 102.0, 100.0, 99.0] + [99.0] * 20
    m1 = _m1(prices)
    sig = _sig(8, atrd=5.0)
    tr = combo_ht.walk(sig, m1, 0, 1, end_ms=10**12)
    assert tr["exit_reason"] == "½ +3% → BE"
    assert abs(tr["exit"] - 101.5) < 1e-9
    assert abs(tr["gross"] - 100.0 * 1.5) < 1e-6  # 100 qty × (½ × 3 + ½ × 0)


def test_walk_stop_first_when_stop_and_target_share_a_minute():
    prices = [100.0] * 5 + [100.0] * 10
    m1 = _m1(prices)
    m1.high[6] = 107.0  # through the 6% target
    m1.low[6] = 94.0  # and through the 5-point stop in the same minute
    sig = _sig(4, atrd=5.0)
    tr = combo_ht.walk(sig, m1, 0, 1, end_ms=10**12)
    assert tr["exit_reason"] == "SL"
    assert abs(tr["exit"] - 95.0) < 1e-9


def test_walk_full_target_without_partial_in_one_minute():
    prices = [100.0] * 5 + [100.0] * 10
    m1 = _m1(prices)
    m1.high[6] = 106.5
    sig = _sig(4, atrd=5.0)
    tr = combo_ht.walk(sig, m1, 0, 1, end_ms=10**12)
    assert tr["exit_reason"] == "TP 6%"
    assert abs(tr["exit"] - 106.0) < 1e-9
