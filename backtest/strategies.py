"""Strategy runners.

Signals are taken from closed bars and filled on the next bar's open.
Resting grid, stop and target orders fill inside the bar that trades
through them. A stop wins when the same bar also reaches a target.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from .data import Bars, align_asof
from .engine import Book
from .indicators import ema_ribbon, hama_stable, sma, streak, turtle_bias, atr as atr_ind
from .risk import doubling_multiplier, ladder_multiplier, loss_multiplier, stepped_risk

NY = ZoneInfo("America/New_York")


def _in_window(ts: int, start: int, end: int) -> bool:
    return start <= int(ts) < end


def _hit_adverse(side: int, open_: float, high: float, low: float, level: float) -> float | None:
    if side == 1:
        if open_ <= level:
            return open_
        if low <= level:
            return level
    else:
        if open_ >= level:
            return open_
        if high >= level:
            return level
    return None


def _hit_favorable(side: int, open_: float, high: float, low: float, level: float) -> float | None:
    if side == 1:
        if open_ >= level:
            return open_
        if high >= level:
            return level
    else:
        if open_ <= level:
            return open_
        if low <= level:
            return level
    return None


def run_hama(bars: Bars, book: Book, cfg: dict, test_start: int, test_end: int) -> None:
    _, _, colour, _ = hama_stable(bars.open, bars.high, bars.low, bars.close, int(cfg["hama_length"]))
    atr_v = atr_ind(bars.high, bars.low, bars.close, int(cfg.get("atr_length", 14)))
    stop_mult = float(cfg.get("stop_atr", 1.0))
    unit = float(cfg["risk"])
    max_step = int(cfg.get("max_step") or 1)
    desired = 0
    armed = 0
    losses = 0
    stop_px = None

    def settle(pnl: float) -> None:
        nonlocal losses, stop_px
        stop_px = None
        if max_step <= 1:
            return
        losses = losses + 1 if pnl < 0 else 0

    def open_hama(ts: int, raw: float, high: float, low: float, side: int, atr_now: float) -> bool:
        nonlocal stop_px
        if atr_now <= 0 or np.isnan(atr_now):
            stop_px = None
            return False
        risk = stepped_risk(unit, losses, max_step)
        stop = raw - side * atr_now * stop_mult
        qty = book.open_risk(ts, raw, stop, risk, side, "entry", high, low)
        stop_px = stop if qty else None
        return True

    n = len(bars)
    for i in range(n):
        ts = int(bars.open_time[i])
        in_window = _in_window(ts, test_start, test_end)
        o = float(bars.open[i])
        h = float(bars.high[i])
        l = float(bars.low[i])
        if in_window and desired != 0 and desired != armed:
            if book.lots:
                settle(book.close_all(ts, o, "flip"))
            atr_now = float(atr_v[i - 1]) if i else float("nan")
            if open_hama(ts, o, h, l, desired, atr_now):
                armed = desired
        if book.lots and in_window:
            book.mark(h, l)
            if stop_px is not None:
                hit = _hit_adverse(book.side, o, h, l, stop_px)
                if hit is not None:
                    settle(book.close_all(ts, hit, "SL"))
        if colour[i] != 0:
            desired = int(colour[i])
        if in_window:
            book.snapshot(int(bars.open_time[i] + _tf_ms(cfg)), float(bars.close[i]))
    if book.lots:
        last = n - 1
        book.close_all(int(bars.open_time[last]), float(bars.close[last]), "end of test")
        book.snapshot(int(bars.open_time[last]), float(bars.close[last]))


def _tf_ms(cfg: dict) -> int:
    tf = cfg.get("exec_tf", "1m")
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[tf]
    return minutes * 60_000 - 1


def lock_bias_at_ny(open_time: np.ndarray, bias: np.ndarray, interval_min: int, hour: int) -> np.ndarray:
    """Forward-fill `bias`, replacing it only on bars that close at `hour:00` New York."""
    out = np.zeros(len(bias), dtype=int)
    current = 0
    span = interval_min * 60_000
    for i in range(len(bias)):
        close_ms = int(open_time[i]) + span
        closed = datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc).astimezone(NY)
        if closed.hour == hour and closed.minute == 0 and closed.second == 0:
            current = int(bias[i])
        out[i] = current
    return out


def run_s5(m15: Bars, m30: Bars, book: Book, cfg: dict, test_start: int, test_end: int) -> None:
    close = m15.close
    sma_len = int(cfg["sma_length"])
    sma_v = sma(close, sma_len)
    atr_v = atr_ind(m15.high, m15.low, close, int(cfg["atr_length"]))
    raw = turtle_bias(m30.high, m30.low, m30.close, int(cfg["turtle_length"]))
    locked = lock_bias_at_ny(m30.open_time, raw, 30, int(cfg["bias_hour_ny"]))
    published = m30.open_time + 30 * 60_000
    close_times = m15.open_time + 15 * 60_000
    bias_at_close = align_asof(published, locked, close_times)

    pending = None
    frozen_atr = None
    first_anchor = None
    original_stop = None
    tp1_done = False
    added = False
    basket_pnl = 0.0
    losses = 0
    tp1_mult = float(cfg["tp1_atr"])
    tp2_mult = float(cfg["tp2_atr"])
    sl_mult = float(cfg["sl_atr"])
    add_mult = float(cfg["add_atr"])
    fraction = float(cfg["tp1_fraction"])

    def live_risk() -> float:
        mode = cfg.get("loss_mode", "after")
        if mode == "double":
            mult = doubling_multiplier(losses, float(cfg["mult_cap"]))
        else:
            mult = loss_multiplier(losses, int(cfg.get("loss_after", 2)), float(cfg.get("loss_mult", 2)), float(cfg.get("mult_cap", 2)))
        return float(cfg["risk"]) * mult

    def settle(pnl: float) -> None:
        nonlocal basket_pnl, losses
        basket_pnl += pnl
        if book.lots:
            return
        losses = losses + 1 if basket_pnl < 0 else 0
        basket_pnl = 0.0

    def reset_flags():
        nonlocal frozen_atr, first_anchor, original_stop, tp1_done, added
        frozen_atr = None
        first_anchor = None
        original_stop = None
        tp1_done = False
        added = False

    n = len(m15)
    for i in range(n):
        ts = int(m15.open_time[i])
        in_window = _in_window(ts, test_start, test_end)
        o = float(m15.open[i])
        h = float(m15.high[i])
        l = float(m15.low[i])
        if pending:
            action = pending
            pending = None
            if in_window:
                if book.lots:
                    settle(book.close_all(ts, o, "flip"))
                    reset_flags()
                if action["side"] != 0 and action["atr"] > 0:
                    side = action["side"]
                    stop = o - side * sl_mult * action["atr"]
                    qty = book.open_risk(ts, o, stop, live_risk(), side, "entry", h, l)
                    if qty:
                        frozen_atr = action["atr"]
                        original_stop = stop
                        first_anchor = o
                        tp1_done = False
                        added = False
        if book.lots and in_window and frozen_atr:
            book.mark(h, l)
            side = book.side
            avg = book.avg_entry()
            dist = frozen_atr
            sl = original_stop if original_stop is not None else avg - side * sl_mult * dist
            tp1 = avg + side * tp1_mult * dist
            tp2 = avg + side * tp2_mult * dist
            add_level = first_anchor - side * add_mult * dist
            stopped = _hit_adverse(side, o, h, l, sl)
            if stopped is not None:
                settle(book.close_all(ts, stopped, "SL"))
                reset_flags()
            else:
                tp2_fill = _hit_favorable(side, o, h, l, tp2)
                tp1_fill = _hit_favorable(side, o, h, l, tp1)
                if tp2_fill is not None:
                    if not tp1_done:
                        settle(book.close_fraction(ts, tp1 if tp2_fill != o else tp2_fill, fraction, "partial TP"))
                        tp1_done = True
                    if book.lots:
                        settle(book.close_all(ts, tp2_fill, "partial TP"))
                    reset_flags()
                elif tp1_fill is not None and not tp1_done:
                    settle(book.close_fraction(ts, tp1_fill, fraction, "partial TP"))
                    tp1_done = True
                elif (not added) and book.lots and original_stop is not None:
                    add_fill = _hit_adverse(side, o, h, l, add_level)
                    if add_fill is not None:
                        book.open_risk(ts, add_fill, original_stop, live_risk(), side, "add", h, l)
                        added = True
        if i > 0 and not np.isnan(sma_v[i]) and not np.isnan(sma_v[i - 1]) and not np.isnan(atr_v[i]):
            b = int(bias_at_close[i])
            if book.lots and b != 0 and b != book.side:
                pending = {"side": b, "atr": float(atr_v[i])}
            elif not book.lots and b != 0:
                long_cross = b == 1 and close[i] > sma_v[i] and close[i - 1] <= sma_v[i - 1]
                short_cross = b == -1 and close[i] < sma_v[i] and close[i - 1] >= sma_v[i - 1]
                if long_cross or short_cross:
                    pending = {"side": b, "atr": float(atr_v[i])}
        if in_window:
            book.snapshot(int(m15.open_time[i] + 15 * 60_000 - 1), float(m15.close[i]))
    if book.lots:
        last = n - 1
        settle(book.close_all(int(m15.open_time[last]), float(m15.close[last]), "end of test"))
        book.snapshot(int(m15.open_time[last]), float(m15.close[last]))


def _direction_for(cfg: dict, signal: Bars, exec_bars: Bars) -> np.ndarray:
    if "direction_values" in cfg:
        return np.asarray(cfg["direction_values"], dtype=int)
    kind = cfg["direction"]
    if kind == "hama":
        _, _, colour, _ = hama_stable(signal.open, signal.high, signal.low, signal.close, int(cfg["hama_length"]))
        values = colour
    elif kind == "ribbon":
        fast, mid, slow = cfg["ribbon"]
        values = ema_ribbon(signal.close, int(fast), int(mid), int(slow))
    else:
        raise ValueError(kind)
    if cfg["signal_tf"] == cfg["exec_tf"]:
        # Colour of a bar is known at its close, so the next bar may trade it.
        out = np.zeros(len(exec_bars), dtype=int)
        out[1:] = values[:-1]
        return out
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[cfg["signal_tf"]]
    published = signal.open_time + minutes * 60_000
    return align_asof(published, values, exec_bars.open_time)


def _basket_closed(book: Book, pnl: float, state: dict, cfg: dict) -> None:
    state["basket_pnl"] += pnl
    if book.lots:
        return
    mode = cfg.get("loss_mode")
    if mode == "double":
        if state["basket_pnl"] < 0:
            state["loss_baskets"] += 1
        else:
            state["loss_baskets"] = 0
        state["size_mult"] = doubling_multiplier(state["loss_baskets"], float(cfg["mult_cap"]))
    elif mode == "ladder" or cfg.get("loss_every"):
        if state["basket_pnl"] < 0:
            state["loss_baskets"] += 1
        else:
            state["loss_baskets"] = 0
        every = int(cfg.get("loss_every") or 0)
        state["size_mult"] = ladder_multiplier(state["loss_baskets"], every, int(cfg.get("max_mult") or 1))
    elif cfg.get("loss_mult"):
        if state["basket_pnl"] < 0:
            state["size_mult"] = min(float(cfg["max_mult"]), state["size_mult"] * float(cfg["loss_mult"]))
        else:
            state["size_mult"] = 1.0
    state["basket_pnl"] = 0.0
    state["level_notional"] = None


def run_grid(bars: Bars, signal: Bars, book: Book, cfg: dict, test_start: int, test_end: int) -> None:
    direction = _direction_for(cfg, signal, bars)
    counts = streak(direction)
    needed = int(cfg["count"])
    step_pct = float(cfg["step_pct"]) / 100.0
    stop_pct = cfg.get("stop_pct")
    stop_pct = None if stop_pct in (None, 0) else float(stop_pct) / 100.0
    max_levels = int(cfg["max_levels"])
    base_notional = float(cfg["level_notional"]) if "level_notional" in cfg else 0.0
    use_risk = "risk" in cfg
    flatten_neutral = bool(cfg.get("flatten_on_neutral"))
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[cfg["exec_tf"]]
    bar_ms = minutes * 60_000 - 1
    state = {"size_mult": 1.0, "basket_pnl": 0.0, "level_notional": None, "loss_baskets": 0}

    def open_level(ts: int, price: float, side: int, reason: str, high: float, low: float) -> None:
        if use_risk:
            distance_pct = stop_pct if stop_pct else step_pct
            stop = price * (1.0 - side * distance_pct)
            book.open_risk(ts, price, stop, float(cfg["risk"]) * state["size_mult"], side, reason, high, low)
            return
        state["level_notional"] = base_notional * state["size_mult"]
        book.open_lot(ts, price, state["level_notional"], side, reason, high, low)

    n = len(bars)
    for i in range(n):
        ts = int(bars.open_time[i])
        in_window = _in_window(ts, test_start, test_end)
        o = float(bars.open[i])
        h = float(bars.high[i])
        l = float(bars.low[i])
        d = int(direction[i])
        if book.lots and in_window:
            book.mark(h, l)
        if not in_window:
            continue

        if book.lots and d != book.side and (d != 0 or flatten_neutral):
            pnl = book.close_all(ts, o, "flip")
            _basket_closed(book, pnl, state, cfg)
            if d != 0 and counts[i] >= needed:
                open_level(ts, o, d, "entry", h, l)
            book.snapshot(ts + bar_ms, float(bars.close[i]))
            continue

        if book.lots:
            side = book.side
            if stop_pct is not None:
                stop = book.avg_entry() * (1.0 - side * stop_pct)
                # avg_entry is the slipped fill. The stop distance is measured
                # from that average, which is the price the basket actually paid.
                stopped = _hit_adverse(side, o, h, l, stop)
                if stopped is not None:
                    pnl = book.close_all(ts, stopped, "SL")
                    _basket_closed(book, pnl, state, cfg)
                    book.snapshot(ts + bar_ms, float(bars.close[i]))
                    continue
            last = book.lots[-1]
            if len(book.lots) < max_levels:
                add_level = last.anchor * (1.0 - side * step_pct)
                add_fill = _hit_adverse(side, o, h, l, add_level)
                if add_fill is not None:
                    open_level(ts, add_fill, side, "add", h, l)
                    book.snapshot(ts + bar_ms, float(bars.close[i]))
                    continue
            if len(book.lots) >= 2:
                unload_level = last.anchor * (1.0 + side * step_pct)
                unload_fill = _hit_favorable(side, o, h, l, unload_level)
                if unload_fill is not None:
                    pnl = book.close_last(ts, unload_fill, "unload")
                    state["basket_pnl"] += pnl
        elif d != 0 and counts[i] >= needed:
            open_level(ts, o, d, "entry", h, l)

        book.snapshot(ts + bar_ms, float(bars.close[i]))

    if book.lots:
        last = n - 1
        book.close_all(int(bars.open_time[last]), float(bars.close[last]), "end of test")
        book.snapshot(int(bars.open_time[last]), float(bars.close[last]))
