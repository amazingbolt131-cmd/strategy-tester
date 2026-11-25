"""TradingView-style overview statistics."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from .engine import Book


def _max_run(flags: list[bool]) -> int:
    best = current = 0
    for flag in flags:
        if flag:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _sharpe(times: list[int], equity: list[float]) -> float | None:
    if len(equity) < 3:
        return None
    by_day: dict[str, float] = {}
    for ts, value in zip(times, equity):
        day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[day] = value
    series = list(by_day.values())
    if len(series) < 3:
        return None
    arr = np.asarray(series, dtype=float)
    prev = arr[:-1]
    rets = np.divide(np.diff(arr), prev, out=np.zeros(len(prev)), where=prev != 0)
    if len(rets) < 2:
        return None
    std = float(np.std(rets, ddof=1))
    if std == 0:
        return None
    return float(np.mean(rets) / std * np.sqrt(365.0))


def summarize(book: Book, starting: float) -> dict:
    trades = book.trades
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = float(sum(pnls))
    gross_profit = float(sum(wins))
    gross_loss = float(sum(losses))
    n = len(trades)
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = None  # infinite
    else:
        profit_factor = 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    win_flags = [p > 0 for p in pnls]
    loss_flags = [p < 0 for p in pnls]
    ending = book.equity_value[-1] if book.equity_value else book.cash
    return {
        "net_profit": net,
        "net_profit_pct": (net / starting * 100.0) if starting else 0.0,
        "pnl_pct": (net / starting * 100.0) if starting else 0.0,
        "ending_equity": ending,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "max_drawdown": book.max_dd,
        "max_drawdown_pct": book.max_dd_pct,
        "total_trades": n,
        "win_rate": (len(wins) / n * 100.0) if n else 0.0,
        "avg_trade": (net / n) if n else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": ratio,
        "largest_win": max(wins) if wins else 0.0,
        "largest_loss": min(losses) if losses else 0.0,
        "max_consec_wins": _max_run(win_flags),
        "max_consec_losses": _max_run(loss_flags),
        "sharpe": _sharpe(book.equity_time, book.equity_value),
        "commission_paid": book.commission_paid,
        "starting_balance": starting,
    }
