"""Indicator math.

EMA is seeded with an SMA, which is what TradingView's ta.ema does.
ATR is Wilder's RMA of true range (ta.atr).
HAMA is the smoothed Heikin Ashi: EMAs of OHLC, then the HA recursion,
with a colour lock that refuses a flip until the real close confirms it.
"""

from __future__ import annotations

import numpy as np


def sma(values: np.ndarray, period: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    if period <= 0 or len(values) < period:
        return out
    window = np.cumsum(values)
    out[period - 1] = window[period - 1] / period
    if len(values) > period:
        out[period:] = (window[period:] - window[:-period]) / period
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = float(np.mean(values[:period]))
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev = np.empty_like(close)
    prev[0] = close[0]
    prev[1:] = close[:-1]
    return np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = true_range(high, low, close)
    out = np.full(len(tr), np.nan)
    if period <= 0 or len(tr) < period:
        return out
    prev = float(np.mean(tr[:period]))
    out[period - 1] = prev
    for i in range(period, len(tr)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def apply_colour_lock(raw: np.ndarray, close: np.ndarray, ha_open: np.ndarray) -> np.ndarray:
    """Lock the HAMA colour.

    The first finite bar adopts the raw colour. After that the lock flips
    only when the raw colour disagrees with the lock and the real close has
    finished beyond the HAMA open (above it to go green, below it to go red).
    """
    raw = np.asarray(raw)
    close = np.asarray(close, dtype=float)
    ha_open = np.asarray(ha_open, dtype=float)
    locked = np.zeros(len(raw), dtype=int)
    state = 0
    for i in range(len(raw)):
        colour = int(raw[i])
        if colour == 0 or np.isnan(ha_open[i]) or np.isnan(close[i]):
            locked[i] = state
            continue
        if state == 0:
            state = colour
        elif colour != state:
            if colour == 1 and close[i] > ha_open[i]:
                state = 1
            elif colour == -1 and close[i] < ha_open[i]:
                state = -1
        locked[i] = state
    return locked


def hama_stable(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int):
    """Return ha_open, ha_close, locked colour (+1/-1/0), raw colour."""
    mo = ema(open_, length)
    mh = ema(high, length)
    ml = ema(low, length)
    mc = ema(close, length)
    ha_close = (mo + mh + ml + mc) / 4.0
    ha_open = np.full(len(close), np.nan)
    start = None
    for i in range(len(close)):
        if np.isnan(ha_close[i]):
            continue
        if start is None:
            start = i
            ha_open[i] = (mo[i] + mc[i]) / 2.0
            continue
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    raw = np.zeros(len(close), dtype=int)
    valid = ~np.isnan(ha_close) & ~np.isnan(ha_open)
    raw[valid] = np.where(ha_close[valid] >= ha_open[valid], 1, -1)
    locked = apply_colour_lock(raw, close, ha_open)
    return ha_open, ha_close, locked, raw


def turtle_bias(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    """Donchian breakout bias. +1 above the prior `length` highs, -1 below the prior lows.

    The current bar is excluded from the channel, and the previous bias is
    held when price is inside the channel.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    bias = np.zeros(len(close), dtype=int)
    state = 0
    for i in range(len(close)):
        if i >= length:
            hh = float(np.max(high[i - length : i]))
            ll = float(np.min(low[i - length : i]))
            if close[i] > hh:
                state = 1
            elif close[i] < ll:
                state = -1
        bias[i] = state
    return bias


def ema_ribbon(close: np.ndarray, fast: int, mid: int, slow: int) -> np.ndarray:
    f = ema(close, fast)
    m = ema(close, mid)
    s = ema(close, slow)
    out = np.zeros(len(close), dtype=int)
    valid = ~np.isnan(f) & ~np.isnan(m) & ~np.isnan(s)
    long = valid & (f > m) & (m > s)
    short = valid & (f < m) & (m < s)
    out[long] = 1
    out[short] = -1
    return out


def streak(direction: np.ndarray) -> np.ndarray:
    """How many bars the current direction has been unchanged, ending here."""
    direction = np.asarray(direction, dtype=int)
    out = np.zeros(len(direction), dtype=int)
    for i, value in enumerate(direction):
        if value == 0:
            out[i] = 0
        elif i > 0 and value == direction[i - 1]:
            out[i] = out[i - 1] + 1
        else:
            out[i] = 1
    return out
