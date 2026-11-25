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


def _time_in_market(trades: list, start_ms: int | None, end_ms: int | None) -> tuple[float | None, float]:
    if not trades:
        return (0.0 if (start_ms is not None and end_ms is not None and end_ms > start_ms) else None), 0.0
    durations = [max(0, t.exit_time - t.entry_time) for t in trades]
    avg_hours = (sum(durations) / len(durations)) / 3_600_000.0
    if start_ms is None or end_ms is None or end_ms <= start_ms:
        return None, avg_hours
    intervals = sorted(
        (max(start_ms, int(t.entry_time)), min(end_ms, int(t.exit_time)))
        for t in trades
        if t.exit_time > start_ms and t.entry_time < end_ms and t.exit_time > t.entry_time
    )
    if not intervals:
        return 0.0, avg_hours
    merged_ms = 0
    cur_s, cur_e = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_e:
            if e > cur_e:
                cur_e = e
        else:
            merged_ms += cur_e - cur_s
            cur_s, cur_e = s, e
    merged_ms += cur_e - cur_s
    pct = min(100.0, merged_ms / float(end_ms - start_ms) * 100.0)
    return pct, avg_hours


def summarize(book: Book, starting: float, start_ms: int | None = None, end_ms: int | None = None) -> dict:
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

    longs = [t for t in trades if t.side == 1]
    shorts = [t for t in trades if t.side == -1]
    long_wins = sum(1 for t in longs if t.pnl > 0)
    short_wins = sum(1 for t in shorts if t.pnl > 0)
    exit_reasons: dict[str, int] = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    time_in_market_pct, avg_duration_hours = _time_in_market(trades, start_ms, end_ms)
    avg_trade = (net / n) if n else 0.0

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
        "return_dd_ratio": (net / book.max_dd) if book.max_dd > 0 else None,
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n * 100.0) if n else 0.0,
        "avg_trade": avg_trade,
        "expectancy": avg_trade,
        "expectancy_pct": (avg_trade / starting * 100.0) if starting else 0.0,
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
        "long_trades": len(longs),
        "long_wins": long_wins,
        "long_win_rate": (long_wins / len(longs) * 100.0) if longs else 0.0,
        "long_net_profit": float(sum(t.pnl for t in longs)),
        "short_trades": len(shorts),
        "short_wins": short_wins,
        "short_win_rate": (short_wins / len(shorts) * 100.0) if shorts else 0.0,
        "short_net_profit": float(sum(t.pnl for t in shorts)),
        "avg_run_up": float(np.mean([t.run_up for t in trades])) if trades else 0.0,
        "avg_drawdown": float(np.mean([t.drawdown for t in trades])) if trades else 0.0,
        "time_in_market_pct": time_in_market_pct,
        "avg_duration_hours": avg_duration_hours,
        "exit_reasons": exit_reasons,
    }
