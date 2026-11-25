"""Indicator formulas: SMA, EMA (SMA seed), Wilder ATR, HAMA colour lock, Turtle."""

import numpy as np

from backtest.indicators import apply_colour_lock, atr, ema, hama_stable, sma, turtle_bias


def test_sma_period_three():
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(values, 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert np.allclose(out[2:], [2.0, 3.0, 4.0])


def test_ema_matches_tradingview_seed():
    # period 3, k = 0.5. Seed is the SMA of the first 3 values (2).
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ema(values, 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == 2.0
    assert out[3] == 4 * 0.5 + 2 * 0.5
    assert out[4] == 5 * 0.5 + 3 * 0.5


def test_wilder_atr():
    high = np.array([10.0, 11.0, 12.0, 11.0, 13.0])
    low = np.array([9.0, 10.0, 10.0, 10.0, 11.0])
    close = np.array([9.5, 10.5, 11.0, 10.5, 12.5])
    out = atr(high, low, close, 3)
    # TR: 1, 1.5, 2, 1, 2.5. ATR seeds at the mean of the first 3.
    assert abs(out[2] - (1 + 1.5 + 2) / 3) < 1e-12
    assert abs(out[3] - ((1.5 * 2) + 1) / 3) < 1e-12
    assert abs(out[4] - ((4 / 3 * 2) + 2.5) / 3) < 1e-9


def test_colour_lock_waits_for_close_confirmation():
    raw = np.array([1, 1, -1, -1])
    close = np.array([10.0, 10.0, 10.0, 9.0])
    ha_open = np.array([9.0, 9.0, 9.0, 9.5])
    locked = apply_colour_lock(raw, close, ha_open)
    # Bar 3 is raw-red but the close is still above the HAMA open, so the
    # lock stays green. Bar 4 closes below the HAMA open and flips.
    assert list(locked) == [1, 1, 1, -1]


def test_hama_close_is_average_of_emas():
    open_ = np.array([1, 2, 3, 4, 5, 6, 7], dtype=float)
    high = open_ + 1
    low = open_ - 0.5
    close = open_ + 0.25
    ha_open, ha_close, locked, raw = hama_stable(open_, high, low, close, 3)
    mo, mh, ml, mc = ema(open_, 3), ema(high, 3), ema(low, 3), ema(close, 3)
    for i in range(len(close)):
        if np.isnan(ha_close[i]):
            assert locked[i] == 0
            continue
        assert abs(ha_close[i] - (mo[i] + mh[i] + ml[i] + mc[i]) / 4) < 1e-9
        assert raw[i] in (1, -1)
        assert locked[i] in (1, -1)


def test_turtle_breakout_uses_prior_channel_only():
    high = np.array([1, 2, 3, 4, 5, 6], dtype=float)
    low = np.array([0, 1, 2, 3, 4, 5], dtype=float)
    close = np.array([0.5, 1.5, 2.5, 3.5, 4.5, 0.2], dtype=float)
    bias = turtle_bias(high, low, close, 3)
    # i=3: prior high max(1,2,3)=3, close 3.5 breaks up.
    assert bias[3] == 1
    # i=4 still inside / above, bias holds long.
    assert bias[4] == 1
    # i=5: prior low min(3,4,5)=3, close 0.2 breaks down.
    assert bias[5] == -1
    assert bias[0] == 0
