"""Load futures klines and run one strategy."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from . import combo_ht
from . import research_strats
from .config import SYMBOLS, resolve_strategy
from .data import TF_MINUTES, Bars, load_tf, test_window
from .engine import Book, Trade
from .stats import summarize
from .v31 import run_engine, settle


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _slice(bars: Bars, start: int, end: int) -> Bars:
    mask = (bars.open_time >= start) & (bars.open_time < end)
    return Bars(
        open_time=bars.open_time[mask],
        open=bars.open[mask],
        high=bars.high[mask],
        low=bars.low[mask],
        close=bars.close[mask],
        volume=bars.volume[mask],
    )


def _downsample(times: list[int], values: list[float], limit: int = 1500):
    n = len(times)
    if n <= limit:
        return times, values
    step = math.ceil(n / limit)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [times[i] for i in idx], [values[i] for i in idx]


def _snap(ts: int, opens: np.ndarray) -> int:
    i = int(np.searchsorted(opens, ts, side="right") - 1)
    i = max(0, min(i, len(opens) - 1))
    return int(opens[i] // 1000)


def _book_from_engine(raw: list[dict], balance: float, commission: float, slippage: float, start: int) -> Book:
    book = Book(cash=balance, commission_rate=commission, slippage_rate=slippage, step=1.0)
    equity = balance
    book.equity_time = [int(start)]
    book.equity_value = [balance]
    book.peak = balance
    ordered = sorted(raw, key=lambda item: (int(item["exit_ms"]), int(item["entry_ms"])))
    for item in ordered:
        net, fee = settle(item, commission, slippage)
        qty = float(item["qty"])
        entry = float(item["entry"])
        notional = abs(entry * qty)
        book.trades.append(
            Trade(
                side=int(item["side"]),
                qty=qty,
                entry_time=int(item["entry_ms"]),
                entry_price=entry,
                exit_time=int(item["exit_ms"]),
                exit_price=float(item["exit"]),
                entry_reason=str(item["entry_reason"]),
                exit_reason=str(item["exit_reason"]),
                pnl=net,
                pnl_pct=(net / notional * 100.0) if notional else 0.0,
                commission=fee,
                run_up=float(item.get("run_up") or 0.0),
                drawdown=float(item.get("drawdown") or 0.0),
            )
        )
        book.commission_paid += fee
        equity += net
        book.equity_time.append(int(item["exit_ms"]))
        book.equity_value.append(equity)
        if equity > book.peak:
            book.peak = equity
        dd = book.peak - equity
        if dd > book.max_dd:
            book.max_dd = dd
            book.max_dd_pct = (dd / book.peak * 100.0) if book.peak else 0.0
    book.cash = equity
    return book


CHART_MAX_BARS = 20_000
_CHART_STEPS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")


def _chart_tf(native: str, start: int, end: int) -> str:
    """The strategy's chart timeframe, or a coarser one so long periods stay light."""
    span_min = max(1, (end - start) // 60_000)
    idx = _CHART_STEPS.index(native) if native in _CHART_STEPS else 0
    for tf in _CHART_STEPS[idx:]:
        if span_min / TF_MINUTES[tf] <= CHART_MAX_BARS:
            return tf
    return _CHART_STEPS[-1]


def run_backtest(
    strategy_id: str,
    symbol: str,
    balance: float,
    commission_pct: float,
    slippage_pct: float,
    window: tuple[int, int, int] | None = None,
    venue: str = "pivex",
    k: float = 8.0,
    daily_filter: bool = True,
    include_chart: bool = True,
    profit_target: float | None = None,
    skip_missing_days: bool = False,
    with_trades: bool = False,
    period: str | None = None,
) -> dict:
    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown symbol {symbol}")
    if balance <= 0:
        raise ValueError("Starting balance must be positive")
    if commission_pct < 0 or slippage_pct < 0:
        raise ValueError("Commission and slippage cannot be negative")

    cfg = resolve_strategy(strategy_id, venue)
    warmup, start, end = window or test_window()
    if research_strats.is_research(strategy_id):
        per = period
        if not per:
            start_ms = (window or (0, 0, 0))[1]
            end_ms = (window or (0, 0, 0))[2]
            if start_ms and start_ms <= 1_701_388_800_000 + 2 * 86_400_000:
                per = "3y"
            elif end_ms and (end_ms - start_ms) >= 300 * 86_400_000:
                per = "1y"
            else:
                per = "1y" if not start_ms else None
        return research_strats.run_precomputed(
            strategy_id, symbol, per or "1y", float(balance),
            include_chart=include_chart, with_trades=with_trades,
        )
    if strategy_id == combo_ht.STRATEGY_ID:
        engine = combo_ht.run_engine(symbol, warmup, start, end, skip_missing_days=skip_missing_days)
    else:
        engine = run_engine(
            strategy_id, symbol, venue, warmup, start, end,
            k=k, daily_filter=daily_filter,
            profit_target=profit_target,
            skip_missing_days=skip_missing_days,
        )
    commission = float(commission_pct) / 100.0
    slippage = float(slippage_pct) / 100.0
    book = _book_from_engine(engine["trades"], float(balance), commission, slippage, start)
    book.finish()
    stats = summarize(book, float(balance), start_ms=start, end_ms=end)
    if not include_chart:
        return {
            "strategy_id": strategy_id,
            "strategy": cfg["label"],
            "venue": venue,
            "venue_label": cfg["venue_label"],
            "symbol": symbol,
            "symbol_label": SYMBOLS[symbol]["label"],
            "rules": list(cfg["rules"]) + ([engine["meta"].get("note", "")] if engine["meta"].get("note") else []),
            "note": engine["meta"].get("note", ""),
            "k": k,
            "daily_filter": daily_filter and venue == "pivex",
            "stats": stats,
            "trades": [
                {
                    "side": t.side,
                    "entry_ms": t.entry_time,
                    "exit_ms": t.exit_time,
                    "pnl": t.pnl,
                    "commission": t.commission,
                }
                for t in book.trades
            ] if with_trades else [],
            "chart": None,
            "window_label": f"{_iso(start)} → {_iso(end)} UTC",
            "window_start": _iso(start),
            "window_end": _iso(end),
        }
    chart_bars = load_tf(symbol, _chart_tf(cfg["chart_tf"], start, end), start, end)
    chart = _slice(chart_bars, start, end)
    eq_t, eq_v = _downsample(book.equity_time, book.equity_value)

    peak = None
    equity = []
    drawdown = []
    last_stamp = -1
    for ts, value in zip(eq_t, eq_v):
        stamp = int(ts // 1000)
        if stamp <= last_stamp:
            continue
        last_stamp = stamp
        peak = value if peak is None else max(peak, value)
        equity.append({"time": stamp, "value": round(value, 2)})
        dd = 0.0 if not peak else (value - peak) / peak * 100.0
        drawdown.append({"time": stamp, "value": round(dd, 3)})

    candles = [
        {
            "time": int(t // 1000),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
        }
        for t, o, h, l, c in zip(chart.open_time, chart.open, chart.high, chart.low, chart.close)
    ]

    short_reason = {
        "partial TP": "TP",
        "unload": "UL",
        "flip": "X",
        "SL": "SL",
        "end of test": "E",
        "entry": "EN",
        "add": "ADD",
    }
    markers = []
    if len(chart):
        for trade in book.trades:
            markers.append(
                {
                    "time": _snap(trade.entry_time, chart.open_time),
                    "position": "belowBar" if trade.side == 1 else "aboveBar",
                    "color": "#26a69a" if trade.side == 1 else "#ef5350",
                    "shape": "arrowUp" if trade.side == 1 else "arrowDown",
                    "text": "L" if trade.side == 1 else "S",
                }
            )
            markers.append(
                {
                    "time": _snap(trade.exit_time, chart.open_time),
                    "position": "aboveBar" if trade.side == 1 else "belowBar",
                    "color": "#b2b5be",
                    "shape": "circle",
                    "text": short_reason.get(trade.exit_reason, trade.exit_reason),
                }
            )
        markers.sort(key=lambda m: (m["time"], m["text"]))

    trades = []
    for i, trade in enumerate(book.trades, start=1):
        trades.append(
            {
                "n": i,
                "side": "Long" if trade.side == 1 else "Short",
                "entry_time": _iso(trade.entry_time),
                "entry_price": trade.entry_price,
                "exit_time": _iso(trade.exit_time),
                "exit_price": trade.exit_price,
                "reason": f"{trade.entry_reason} → {trade.exit_reason}",
                "qty": trade.qty,
                "pnl": trade.pnl,
                "pnl_pct": trade.pnl_pct,
                "cumulative": trade.cumulative,
                "run_up": trade.run_up,
                "drawdown": trade.drawdown,
            }
        )

    return {
        "strategy_id": strategy_id,
        "strategy": cfg["label"],
        "venue": venue,
        "venue_label": cfg["venue_label"],
        "symbol": symbol,
        "symbol_label": SYMBOLS[symbol]["label"],
        "rules": list(cfg["rules"]) + [engine["meta"].get("note", "")],
        "note": engine["meta"].get("note", ""),
        "k": k,
        "daily_filter": daily_filter and venue == "pivex",
        "stats": stats,
        "trades": trades,
        "chart": {"equity": equity, "drawdown": drawdown, "candles": candles, "markers": markers},
        "window_label": f"{_iso(start)} → {_iso(end)} UTC",
        "window_start": _iso(start),
        "window_end": _iso(end),
    }
