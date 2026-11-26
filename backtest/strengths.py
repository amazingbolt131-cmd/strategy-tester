"""Strengths run: every strategy on both venues, one coin, one period, ranked together.

Each strategy runs under its own venue's rules (Pivex single-entry, k-scaled size and
the daily HAMA filter setting; Binance multi-entry), with the commission and slippage
from the form. Rows are then scored across categories so each strategy x timeframe x
venue gets a "Good for" list and a weaknesses list.
"""

from __future__ import annotations

import gc
import math
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np

from .config import (
    DEFAULT_K,
    PERIODS,
    SYMBOLS,
    TESTER_STRATEGIES,
    VENUE_LABELS,
    VENUE_ORDER,
    symbols_for,
    venue_enabled,
)
from .data import clear_memo, load_1m, period_window
from .runner import run_backtest

try:  # Kyiv calendar days for "worst day".
    from zoneinfo import ZoneInfo

    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001 - tzdata missing
    KYIV = timezone(timedelta(hours=2))

DAY_MS = 86_400_000
MIN_TRADES = 3
PIVEX_DD_LIMIT = 600.0
PIVEX_TARGET = 1000.0
HEAVY_1M = {"s5_15m", "dg_grid", "er_grid", "hama_grid_5m", "hama_grid_1m", "sol1m_morning", "combo_ht_5m"}

TIMEFRAMES = {
    "micro_1h": "1h",
    "turtle_5m": "5m (30m bias)",
    "ml_5m": "5m",
    "hama_30m": "30m (1h lock)",
    "hama_1h": "1h (4h lock)",
    "s5_15m": "15m (30m bias, 1m fills)",
    "sol1m_morning": "1m (3m lock)",
    "dg_grid": "1h / 1m",
    "er_grid": "15m / 1m",
    "hama_grid_5m": "5m",
    "hama_grid_1m": "1m (15m lock)",
    "combo_ht_5m": "5m + 1h trend",
}

VENUE_RULES = {
    "pivex": "Pivex rules: single entry, one stop and one target, no adds; Pivex size (unit × weight × k=8); daily HAMA filter as set on the form. $600 drawdown and +$1,000 target shown for info.",
    "binance": "Binance rules: full multi-entry logic (adds, partial TPs, grid levels) with Binance card sizes.",
}

# key, label, higher is better, needs net > 0, strength text, weakness text
CATEGORIES = [
    ("net_profit", "Most profit", True, False, "Most profit", "Lowest profit"),
    ("return_pct", "Best return %", True, False, "Best return on balance", "Worst return on balance"),
    ("profit_factor", "Best profit factor", True, False, "High profit factor", "Low profit factor"),
    ("win_rate", "Best win rate", True, False, "High win rate", "Low win rate"),
    ("max_dd_pct", "Lowest drawdown", False, True, "Low drawdown", "Deep drawdown"),
    ("recovery_factor", "Best recovery factor", True, False, "Recovers fast from drawdowns", "Slow to recover from drawdowns"),
    ("rr", "Best reward:risk", True, False, "Big wins vs losses (R:R)", "Small wins vs losses (R:R)"),
    ("expectancy", "Best expectancy", True, False, "Best $ per trade", "Worst $ per trade"),
    ("green_pct", "Most consistent", True, False, "Consistent (most green weeks/months)", "Inconsistent (few green weeks/months)"),
    ("period_std", "Smoothest P&L", False, True, "Smooth P&L (low weekly/monthly swings)", "Lumpy P&L (big weekly/monthly swings)"),
    ("sharpe", "Best Sharpe", True, False, "Best Sharpe (return per volatility)", "Poor Sharpe"),
    ("sortino", "Best Sortino", True, False, "Best Sortino (return per downside)", "Poor Sortino"),
    ("max_loss_streak", "Shortest losing streak", False, False, "Short losing streaks", "Long losing streaks"),
    ("max_win_streak", "Longest winning streak", True, False, "Long winning streaks", None),
    ("long_net", "Best long side", True, False, "Strong long side", "Weak long side"),
    ("short_net", "Best short side", True, False, "Strong short side", "Weak short side"),
    ("best_trade", "Biggest winner", True, False, "Catches big winners", None),
    ("worst_trade", "Mildest worst trade", True, False, "Small worst loss", "Large single loss"),
    ("worst_day", "Mildest worst day", True, False, "Mild worst day", "Bad worst day"),
    ("fee_drag_pct", "Lowest fee drag", False, True, "Fees barely matter", "Fees eat the edge"),
    ("time_in_market_pct", "Least time in market", False, True, "Profits with little time in market", None),
    ("avg_hold_h", "Fastest trades", False, True, "Quick in-and-out trades", None),
    ("trades", "Most active", True, False, "Most trade opportunities", None),
]
CATEGORY_KEYS = [c[0] for c in CATEGORIES]


def coin_choices() -> list[dict]:
    seen: list[str] = []
    for strategy in TESTER_STRATEGIES:
        for venue in VENUE_ORDER:
            if not venue_enabled(strategy, venue):
                continue
            for symbol in symbols_for(strategy["id"], venue):
                if symbol not in seen:
                    seen.append(symbol)
    return [{"id": s, "label": SYMBOLS[s]["label"]} for s in seen]


def strengths_plan(symbol: str, all_coins: bool = False) -> tuple[list[dict], list[dict]]:
    """(rows to run, rows skipped because the coin is not offered).

    With all_coins, every strategy runs on the coin even when it is not one of
    its live coins; those rows carry off_list=True and a note saying so.
    """
    run_rows: list[dict] = []
    skipped: list[dict] = []
    for venue in VENUE_ORDER:
        for strategy in TESTER_STRATEGIES:
            if not venue_enabled(strategy, venue):
                continue
            row = {
                "key": f"{venue}:{strategy['id']}",
                "venue": venue,
                "venue_label": VENUE_LABELS[venue],
                "strategy_id": strategy["id"],
                "strategy": strategy["label"],
                "timeframe": TIMEFRAMES.get(strategy["id"], strategy.get("chart_tf", "")),
                "symbol": symbol,
                "symbol_label": SYMBOLS[symbol]["label"],
            }
            coins = symbols_for(strategy["id"], venue)
            if symbol in coins:
                run_rows.append(row)
            elif all_coins:
                row["off_list"] = True
                row["list_note"] = (
                    f"{SYMBOLS[symbol]['label']} is not one of {strategy['label']}'s live coins on {VENUE_LABELS[venue]} "
                    f"({', '.join(SYMBOLS[c]['label'] for c in coins)}). Tested anyway because "
                    "\"every strategy on every coin\" is on."
                )
                run_rows.append(row)
            else:
                row["note"] = (
                    f"{strategy['label']} on {VENUE_LABELS[venue]} is not offered on {SYMBOLS[symbol]['label']}. "
                    f"Its coins: {', '.join(SYMBOLS[c]['label'] for c in coins)}."
                )
                skipped.append(row)
    return run_rows, skipped


def _bucket_pnls(trades: list[dict], start: int, end: int) -> tuple[str, list[float]]:
    """Weekly buckets for windows up to ~5 weeks, calendar months (UTC) otherwise."""
    if end - start <= 35 * DAY_MS:
        n = max(1, math.ceil((end - start) / (7 * DAY_MS)))
        out = [0.0] * n
        for t in trades:
            i = min(n - 1, max(0, (int(t["exit_ms"]) - start) // (7 * DAY_MS)))
            out[i] += float(t["pnl"])
        return "weeks", out
    months: dict[str, float] = {}
    cur = datetime.fromtimestamp(start / 1000, tz=timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = datetime.fromtimestamp((end - 1) / 1000, tz=timezone.utc)
    while cur <= last:
        months[cur.strftime("%Y-%m")] = 0.0
        cur = (cur + timedelta(days=32)).replace(day=1)
    for t in trades:
        key = datetime.fromtimestamp(int(t["exit_ms"]) / 1000, tz=timezone.utc).strftime("%Y-%m")
        if key in months:
            months[key] += float(t["pnl"])
    return "months", list(months.values())


def _daily(trades: list[dict], start: int, end: int, balance: float) -> tuple[float | None, float | None, float]:
    """Sharpe and Sortino on calendar-day P&L (UTC), plus the worst Kyiv day."""
    days = max(1, math.ceil((end - start) / DAY_MS))
    utc = np.zeros(days)
    kyiv: dict[str, float] = {}
    for t in trades:
        ms = int(t["exit_ms"])
        i = min(days - 1, max(0, (ms - start) // DAY_MS))
        utc[i] += float(t["pnl"])
        key = datetime.fromtimestamp(ms / 1000, tz=KYIV).strftime("%Y-%m-%d")
        kyiv[key] = kyiv.get(key, 0.0) + float(t["pnl"])
    rets = utc / balance if balance else utc
    sharpe = sortino = None
    if len(rets) >= 3 and np.any(rets != 0):
        sd = float(np.std(rets, ddof=1))
        if sd > 0:
            sharpe = float(np.mean(rets) / sd * math.sqrt(365.0))
        down = rets[rets < 0]
        if len(down):
            dd = float(math.sqrt(np.mean(np.square(np.minimum(rets, 0.0)))))
            if dd > 0:
                sortino = float(np.mean(rets) / dd * math.sqrt(365.0))
    worst_day = min(kyiv.values()) if kyiv else 0.0
    return sharpe, sortino, min(0.0, worst_day)


def row_metrics(res: dict, balance: float, start: int, end: int) -> dict:
    st = res["stats"]
    trades = res.get("trades") or []
    bucket, pnls = _bucket_pnls(trades, start, end)
    sharpe, sortino, worst_day = _daily(trades, start, end, balance)
    net = float(st["net_profit"])
    fees = float(st["commission_paid"])
    before_fees = net + fees
    peak_net = 0.0
    running = 0.0
    for t in sorted(trades, key=lambda x: (int(x["exit_ms"]), int(x["entry_ms"]))):
        running += float(t["pnl"])
        peak_net = max(peak_net, running)
    m = {
        "net_profit": net,
        "return_pct": float(st["net_profit_pct"]),
        "profit_factor": st["profit_factor"],
        "pf_infinite": st["profit_factor"] is None and st["total_trades"] > 0,
        "win_rate": float(st["win_rate"]),
        "max_dd_pct": float(st["max_drawdown_pct"]),
        "max_dd": float(st["max_drawdown"]),
        "recovery_factor": st["return_dd_ratio"],
        "rr": st["win_loss_ratio"],
        "expectancy": float(st["expectancy"]),
        "trades": int(st["total_trades"]),
        "wins": int(st["wins"]),
        "losses": int(st["losses"]),
        "max_loss_streak": int(st["max_consec_losses"]),
        "max_win_streak": int(st["max_consec_wins"]),
        "bucket": bucket,
        "buckets": len(pnls),
        "green_pct": (sum(1 for p in pnls if p > 0) / len(pnls) * 100.0) if pnls else 0.0,
        "period_std": float(np.std(pnls, ddof=1)) if len(pnls) >= 2 else None,
        "sharpe": sharpe,
        "sortino": sortino,
        "avg_hold_h": float(st["avg_duration_hours"]) if st["total_trades"] else None,
        "long_trades": int(st["long_trades"]),
        "long_net": float(st["long_net_profit"]),
        "long_win_rate": float(st["long_win_rate"]),
        "short_trades": int(st["short_trades"]),
        "short_net": float(st["short_net_profit"]),
        "short_win_rate": float(st["short_win_rate"]),
        "best_trade": float(st["largest_win"]),
        "worst_trade": float(st["largest_loss"]),
        "time_in_market_pct": st["time_in_market_pct"],
        "fees": fees,
        "fee_drag_pct": (fees / before_fees * 100.0) if before_fees > 0 else None,
        "worst_day": worst_day,
        "peak_net": peak_net,
    }
    if res.get("venue") == "pivex":
        m["pivex_dd_breach"] = m["max_dd"] >= PIVEX_DD_LIMIT
        m["pivex_target_hit"] = peak_net >= PIVEX_TARGET
        m["pivex_info"] = (
            f"Max DD ${m['max_dd']:,.0f} {'≥' if m['pivex_dd_breach'] else '<'} $600 · "
            f"best closed P&L +${peak_net:,.0f} {'≥' if m['pivex_target_hit'] else '<'} $1,000"
        )
    return m


def _value(row: dict, key: str):
    m = row.get("metrics") or {}
    if key == "profit_factor" and m.get("pf_infinite"):
        return float("inf")
    v = m.get(key)
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def score_rows(rows: list[dict]) -> list[dict]:
    """Badges for the top 3 and weaknesses for the bottom 3 of each category."""
    ok = [r for r in rows if r.get("metrics")]
    for r in ok:
        r["badges"] = []
        r["weak"] = []
    leaders = []
    for key, label, higher, need_profit, good, bad in CATEGORIES:
        elig = []
        for r in ok:
            m = r["metrics"]
            if m["trades"] < MIN_TRADES:
                continue
            if need_profit and m["net_profit"] <= 0:
                continue
            if key == "long_net" and not m["long_trades"]:
                continue
            if key == "short_net" and not m["short_trades"]:
                continue
            v = _value(r, key)
            if v is None:
                continue
            elig.append((v, r))
        if not elig:
            leaders.append({"key": key, "label": label, "top": []})
            continue
        elig.sort(key=lambda x: (x[0] if higher else -x[0], x[1]["metrics"]["net_profit"]), reverse=True)
        top = elig[:3]
        for rank, (v, r) in enumerate(top, start=1):
            r["badges"].append({"key": key, "label": label, "rank": rank, "text": good})
        leaders.append({
            "key": key,
            "label": label,
            "top": [{"key": r["key"], "rank": i + 1, "value": v if v != float("inf") else None} for i, (v, r) in enumerate(top)],
        })
        if bad and len(elig) >= 6:
            for v, r in elig[-3:]:
                if all(r is not t[1] for t in top):
                    r["weak"].append(bad)
    for r in ok:
        m = r["metrics"]
        if m["trades"] == 0:
            r["weak"].append("No trades in this period")
        elif m["trades"] < MIN_TRADES:
            r["weak"].append(f"Only {m['trades']} trade(s): too few to judge")
        if m["net_profit"] < 0 and "Lowest profit" not in r["weak"]:
            r["weak"].append("Loses money in this period")
        if m["trades"] and (m["net_profit"] + m["fees"]) <= 0:
            r["weak"].append("No edge even before fees")
        elif m["fee_drag_pct"] is not None and m["fee_drag_pct"] >= 50 and "Fees eat the edge" not in r["weak"]:
            r["weak"].append(f"Fees take {m['fee_drag_pct']:.0f}% of the pre-fee profit")
        if r["venue"] == "pivex" and m.get("pivex_dd_breach"):
            r["weak"].append("Drawdown reached Pivex's $600 limit")
        good_extra = []
        if m["trades"] >= MIN_TRADES:
            if m["win_rate"] >= 60:
                good_extra.append(f"Wins {m['win_rate']:.0f}% of trades")
            pf = _value(r, "profit_factor")
            if pf is not None and pf >= 1.5:
                good_extra.append("Profit factor ≥ 1.5" if pf != float("inf") else "No losing trades")
            if m["net_profit"] > 0 and m["max_dd_pct"] < 3:
                good_extra.append(f"Max drawdown under 3% ({m['max_dd_pct']:.1f}%)")
            if m["buckets"] >= 3 and m["green_pct"] >= 75:
                good_extra.append(f"Green in {m['green_pct']:.0f}% of {m['bucket']}")
        if r["venue"] == "pivex" and m.get("pivex_target_hit") and not m.get("pivex_dd_breach"):
            good_extra.append("Reached +$1,000 without a $600 drawdown")
        r["good_extra"] = good_extra
        r["score"] = sum(4 - b["rank"] for b in r["badges"])
    return leaders


ML_PRICE_FLOOR = 0.50
ML_LOW_PRICE_NOTE = (
    "ML Lorentz is known to misbehave on coins priced under $0.50; treat this row as unreliable."
)


def _proc_pss_kb(pid: int) -> int:
    """Proportional set size of one process in kB (shared pages split fairly)."""
    try:
        with open(f"/proc/{pid}/smaps_rollup") as fh:
            for line in fh:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def _proc_tree(pid: int) -> list[int]:
    out = [pid]
    i = 0
    while i < len(out):
        cur = out[i]
        i += 1
        try:
            for tid in os.listdir(f"/proc/{cur}/task"):
                try:
                    with open(f"/proc/{cur}/task/{tid}/children") as fh:
                        out.extend(int(x) for x in fh.read().split())
                except OSError:
                    continue
        except OSError:
            continue
    return out


def row_memory_budget_mb() -> float | None:
    """Memory the whole server may use while one row runs (None = no guard).

    STRENGTHS_MEM_LIMIT_MB wins; on Render the free plan's 512 MB is assumed,
    minus a safety margin, so a heavy row is stopped and noted instead of the
    whole server being killed.
    """
    raw = os.environ.get("STRENGTHS_MEM_LIMIT_MB")
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    if os.environ.get("RENDER"):
        return 470.0
    return None


def _isolate_default() -> bool:
    raw = os.environ.get("STRENGTHS_ISOLATE")
    if raw is not None:
        return raw not in ("0", "false", "False", "")
    return hasattr(os, "fork") and os.path.isdir("/proc")


def _compute_row(row: dict, symbol: str, window, balance, commission, slippage, daily_filter) -> dict:
    _, start, end = window
    res = run_backtest(
        row["strategy_id"], symbol, balance, commission, slippage,
        window=window, venue=row["venue"], k=DEFAULT_K, daily_filter=daily_filter,
        include_chart=False, with_trades=True,
    )
    return {"metrics": row_metrics(res, balance, start, end), "note": res.get("note", "")}


def _child(conn, row, symbol, window, balance, commission, slippage, daily_filter) -> None:
    try:
        out = _compute_row(row, symbol, window, balance, commission, slippage, daily_filter)
        conn.send(("ok", _clean(out)))
    except BaseException as exc:  # noqa: BLE001 - report everything to the parent
        msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else exc.__class__.__name__
        conn.send(("error", msg[:600]))
    finally:
        conn.close()


def _run_row_isolated(row, symbol, window, balance, commission, slippage, daily_filter,
                      budget_mb: float | None, cancel_check=None) -> dict:
    """Run one row in a forked child so a memory blow-up kills only that row."""
    import multiprocessing as mp

    ctx = mp.get_context("fork")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child_conn, row, symbol, window, balance, commission, slippage, daily_filter), daemon=True)
    proc.start()
    child_conn.close()
    peak_mb = 0.0
    result = None
    killed_for = None
    try:
        while True:
            used = sum(_proc_pss_kb(p) for p in [os.getpid()] + _proc_tree(proc.pid)) / 1024.0
            peak_mb = max(peak_mb, used)
            if budget_mb and used > budget_mb:
                killed_for = "memory"
                break
            if parent_conn.poll(0.25):
                try:
                    result = parent_conn.recv()
                except EOFError:
                    result = None
                break
            if not proc.is_alive():
                if parent_conn.poll(0.2):
                    try:
                        result = parent_conn.recv()
                    except EOFError:
                        result = None
                break
            if cancel_check and cancel_check():
                killed_for = "cancel"
                break
    finally:
        if killed_for or proc.is_alive():
            for pid in reversed(_proc_tree(proc.pid)):
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        proc.join(5)
        parent_conn.close()
    if killed_for == "cancel":
        raise RuntimeError("Strengths run cancelled by a newer request")
    if killed_for == "memory":
        return {"error": (
            f"Stopped before the server ran out of memory: this row pushed the server to about {peak_mb:.0f} MB "
            f"(limit {budget_mb:.0f} MB on the free 512 MB plan). Run it on a shorter period or locally."
        ), "peak_mb": round(peak_mb, 1)}
    if result is None:
        code = proc.exitcode
        why = "was killed (most likely out of memory)" if code is not None and code < 0 else f"exited with code {code}"
        return {"error": f"The engine process {why} before finishing this row.", "peak_mb": round(peak_mb, 1)}
    kind, payload = result
    if kind == "ok":
        payload["peak_mb"] = round(peak_mb, 1)
        return payload
    return {"error": payload, "peak_mb": round(peak_mb, 1)}


def _last_close(symbol: str, window) -> float | None:
    warmup, _, end = window
    try:
        bars = load_1m(symbol, warmup, end)
        return float(bars.close[-1]) if len(bars) else None
    except Exception:  # noqa: BLE001
        return None


def run_strengths(
    symbol: str,
    period: str,
    balance: float,
    commission: float,
    slippage: float,
    daily_filter: bool,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    all_coins: bool = False,
    isolate: bool | None = None,
    window: tuple[int, int, int] | None = None,
) -> dict:
    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown symbol {symbol}")
    if period not in PERIODS:
        raise ValueError(f"Unknown period {period}")
    t0 = time.monotonic()
    window = window or period_window(period)
    _, start, end = window
    rows, skipped = strengths_plan(symbol, all_coins=all_coins)
    total = len(rows)
    isolate = _isolate_default() if isolate is None else isolate
    budget = row_memory_budget_mb() if isolate else None
    # Load the coin's 1m tape once in this process; forked rows share it.
    last_close = _last_close(symbol, window)
    for i, row in enumerate(rows):
        if cancel_check and cancel_check():
            raise RuntimeError("Strengths run cancelled by a newer request")
        if progress_cb:
            progress_cb(i, total, f"{row['venue_label']} · {row['strategy']} · {row['symbol_label']}")
        t1 = time.monotonic()
        try:
            if isolate:
                out = _run_row_isolated(row, symbol, window, balance, commission, slippage, daily_filter,
                                        budget, cancel_check=cancel_check)
            else:
                out = _compute_row(row, symbol, window, balance, commission, slippage, daily_filter)
        except Exception as exc:  # noqa: BLE001 - one failed row must not stop the run
            if "cancelled" in str(exc):
                raise
            msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else exc.__class__.__name__
            out = {"error": msg[:600]}
        if out.get("error"):
            row["metrics"] = None
            msg = out["error"]
            if row["strategy_id"] in HEAVY_1M and period in ("6m", "1y") and "memory" not in msg:
                msg += " · This strategy reads 1m candles; a 6- or 12-month run can exceed the free server's memory or time. Try a shorter period."
            row["error"] = msg[:800]
        else:
            row["metrics"] = out["metrics"]
            row["note"] = out.get("note", "")
            row["error"] = None
        if out.get("peak_mb") is not None:
            row["peak_mb"] = out["peak_mb"]
        if row["strategy_id"] == "ml_5m" and last_close is not None and last_close < ML_PRICE_FLOOR:
            row["ml_low_price"] = True
        row["elapsed_s"] = round(time.monotonic() - t1, 1)
        gc.collect()
    leaders = score_rows(rows)
    for row in rows:
        if row.get("ml_low_price"):
            row.setdefault("weak", []).append(ML_LOW_PRICE_NOTE + f" ({SYMBOLS[symbol]['label']} last close ${last_close:.4f})")
        if row.get("off_list"):
            row.setdefault("weak", []).append("Not one of its live coins (tested anyway)")
    rows = _clean(rows)
    leaders = _clean(leaders)
    if progress_cb:
        progress_cb(total, total, "Scoring…")
    stamp = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")  # noqa: E731
    return {
        "symbol": symbol,
        "symbol_label": SYMBOLS[symbol]["label"],
        "period": period,
        "period_label": PERIODS[period]["label"],
        "window_label": f"{stamp(start)} → {stamp(end)} UTC",
        "last_close": last_close,
        "all_coins": all_coins,
        "settings": {
            "balance": balance,
            "commission": commission,
            "slippage": slippage,
            "daily_filter": daily_filter,
            "k": DEFAULT_K,
        },
        "venue_rules": VENUE_RULES,
        "categories": [{"key": c[0], "label": c[1], "higher_is_better": c[2], "needs_profit": c[3]} for c in CATEGORIES],
        "min_trades": MIN_TRADES,
        "rows": rows,
        "skipped": skipped,
        "leaders": leaders,
        "elapsed_s": round(time.monotonic() - t0, 1),
    }


# ---------------------------------------------------------------- multi-coin

MIN_SYMBOLS = 5
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
DEFAULT_MULTI_PERIOD = "1y"
PF_CAP = 99.0  # a coin with no losing trades counts as PF 99 in cross-coin medians

# key, label, higher is better, needs total net > 0, strength text, weakness text
AGG_CATEGORIES = [
    ("profitable_share", "Profitable on most coins", True, False, "Profitable on most coins", "Loses on most coins"),
    ("total_net", "Most total profit", True, False, "Most profit across all coins", "Least profit across all coins"),
    ("median_return_pct", "Best median return %", True, False, "Best typical return per coin", "Worst typical return per coin"),
    ("median_pf", "Best median profit factor", True, False, "High profit factor on a typical coin", "Low profit factor on a typical coin"),
    ("median_win_rate", "Best median win rate", True, False, "High win rate on a typical coin", "Low win rate on a typical coin"),
    ("median_dd_pct", "Lowest median drawdown", False, True, "Low drawdown on a typical coin", "Deep drawdown on a typical coin"),
    ("worst_dd_pct", "Lowest worst-coin drawdown", False, True, "Low drawdown even on its worst coin", "Very deep drawdown on its worst coin"),
    ("worst_return_pct", "Best worst coin", True, False, "Holds up even on its worst coin", "Big loss on its worst coin"),
    ("return_std", "Most consistent across coins", False, True, "Similar results on every coin", "Results swing a lot between coins"),
    ("avg_green_pct", "Most green months", True, False, "Most green months (all coins)", "Few green months"),
    ("median_recovery", "Best median recovery", True, False, "Recovers fast from drawdowns", "Slow to recover from drawdowns"),
    ("median_rr", "Best median R:R", True, False, "Big wins vs losses (R:R)", "Small wins vs losses (R:R)"),
    ("median_expectancy", "Best median $ per trade", True, False, "Best $ per trade", "Worst $ per trade"),
    ("median_sharpe", "Best median Sharpe", True, False, "Best Sharpe across coins", "Poor Sharpe across coins"),
    ("median_sortino", "Best median Sortino", True, False, "Best Sortino across coins", "Poor Sortino across coins"),
    ("max_loss_streak", "Shortest worst losing streak", False, False, "Short losing streaks", "Long losing streaks"),
    ("long_net", "Best long side", True, False, "Strong long side (all coins)", "Weak long side (all coins)"),
    ("short_net", "Best short side", True, False, "Strong short side (all coins)", "Weak short side (all coins)"),
    ("worst_trade", "Mildest worst trade", True, False, "Small worst loss", "Large single loss"),
    ("worst_day", "Mildest worst day", True, False, "Mild worst day", "Bad worst day"),
    ("median_fee_drag", "Lowest fee drag", False, True, "Fees barely matter", "Fees eat the edge"),
    ("median_time_in_market", "Least time in market", False, True, "Profits with little time in market", None),
    ("median_hold_h", "Fastest trades", False, True, "Quick in-and-out trades", None),
    ("total_trades", "Most active", True, False, "Most trade opportunities", None),
]
AGG_KEYS = [c[0] for c in AGG_CATEGORIES]
CATEGORY_LABELS = {c[0]: c[1] for c in CATEGORIES}


def _median(vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return float(statistics.median(vals)) if vals else None


def _mean(vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return float(sum(vals) / len(vals)) if vals else None


def aggregate_reports(reports: list[dict]) -> dict:
    """One row per strategy × venue across all coins, then badges on the aggregate."""
    order: list[str] = []
    by_key: dict[str, list[tuple[dict, dict]]] = {}
    for rep in reports:
        for r in rep["rows"]:
            if r["key"] not in by_key:
                order.append(r["key"])
                by_key[r["key"]] = []
            by_key[r["key"]].append((rep, r))
    agg_rows = []
    for key in order:
        items = by_key[key]
        first = items[0][1]
        # ML Lorentz rows on coins under $0.50 are known-bad, so they are kept in the
        # per-coin table but left out of the cross-coin numbers.
        ok = [(rep, r) for rep, r in items if r.get("metrics") and not r.get("ml_low_price")]
        failed = [{"symbol": r["symbol"], "symbol_label": r["symbol_label"], "error": r.get("error")} for rep, r in items if not r.get("metrics")]
        ms = [r["metrics"] for _, r in ok]
        labels = [r["symbol_label"] for _, r in ok]

        def pf_of(m):
            if m.get("pf_infinite"):
                return PF_CAP
            v = m.get("profit_factor")
            return None if v is None else min(float(v), PF_CAP)

        nets = [m["net_profit"] for m in ms]
        rets = [m["return_pct"] for m in ms]
        n = len(ms)
        prof = [labels[i] for i, m in enumerate(ms) if m["net_profit"] > 0]
        a = {
            "coins_run": n,
            "coins_failed": len(failed),
            "coins_total": len(items),
            "profitable": len(prof),
            "profitable_coins": prof,
            "losing_coins": [labels[i] for i, m in enumerate(ms) if m["net_profit"] <= 0],
            "profitable_share": (len(prof) / n * 100.0) if n else None,
            "total_net": float(sum(nets)) if n else None,
            "mean_net": _mean(nets),
            "median_return_pct": _median(rets),
            "mean_return_pct": _mean(rets),
            "worst_return_pct": min(rets) if rets else None,
            "best_return_pct": max(rets) if rets else None,
            "return_std": float(np.std(rets)) if n >= 2 else None,
            "median_pf": _median([pf_of(m) for m in ms]),
            "mean_pf": _mean([pf_of(m) for m in ms]),
            "median_win_rate": _median([m["win_rate"] for m in ms]),
            "median_dd_pct": _median([m["max_dd_pct"] for m in ms]),
            "worst_dd_pct": max([m["max_dd_pct"] for m in ms]) if ms else None,
            "worst_dd_coin": labels[max(range(n), key=lambda i: ms[i]["max_dd_pct"])] if n else None,
            "avg_green_pct": _mean([m["green_pct"] for m in ms]),
            "median_recovery": _median([m.get("recovery_factor") for m in ms]),
            "median_rr": _median([m.get("rr") for m in ms]),
            "median_expectancy": _median([m["expectancy"] for m in ms]),
            "median_sharpe": _median([m.get("sharpe") for m in ms]),
            "median_sortino": _median([m.get("sortino") for m in ms]),
            "max_loss_streak": max([m["max_loss_streak"] for m in ms]) if ms else None,
            "max_win_streak": max([m["max_win_streak"] for m in ms]) if ms else None,
            "total_trades": int(sum(m["trades"] for m in ms)),
            "long_trades": int(sum(m["long_trades"] for m in ms)),
            "short_trades": int(sum(m["short_trades"] for m in ms)),
            "long_net": float(sum(m["long_net"] for m in ms)) if ms else None,
            "short_net": float(sum(m["short_net"] for m in ms)) if ms else None,
            "best_trade": max([m["best_trade"] for m in ms]) if ms else None,
            "worst_trade": min([m["worst_trade"] for m in ms]) if ms else None,
            "worst_day": min([m["worst_day"] for m in ms]) if ms else None,
            "median_fee_drag": _median([m.get("fee_drag_pct") for m in ms]),
            "total_fees": float(sum(m["fees"] for m in ms)) if ms else None,
            "median_time_in_market": _median([m.get("time_in_market_pct") for m in ms]),
            "median_hold_h": _median([m.get("avg_hold_h") for m in ms]),
            "pivex_dd_breaches": sum(1 for m in ms if m.get("pivex_dd_breach")),
            "pivex_target_hits": sum(1 for m in ms if m.get("pivex_target_hit") and not m.get("pivex_dd_breach")),
        }
        per_coin = []
        badge_coins: dict[str, list[str]] = {}
        for rep, r in items:
            m = r.get("metrics") or {}
            per_coin.append({
                "symbol": r["symbol"],
                "symbol_label": r["symbol_label"],
                "net_profit": m.get("net_profit"),
                "return_pct": m.get("return_pct"),
                "profit_factor": m.get("profit_factor"),
                "pf_infinite": m.get("pf_infinite"),
                "win_rate": m.get("win_rate"),
                "max_dd_pct": m.get("max_dd_pct"),
                "trades": m.get("trades"),
                "score": r.get("score"),
                "off_list": bool(r.get("off_list")),
                "excluded": bool(r.get("ml_low_price")),
                "error": r.get("error"),
            })
            for b in r.get("badges") or []:
                badge_coins.setdefault(b["key"], []).append(r["symbol_label"])
        agg_rows.append({
            "key": key,
            "venue": first["venue"],
            "venue_label": first["venue_label"],
            "strategy_id": first["strategy_id"],
            "strategy": first["strategy"],
            "timeframe": first["timeframe"],
            "metrics": a if n else None,
            "per_coin": per_coin,
            "failed": failed,
            "badge_coins": badge_coins,
            "off_list_coins": [r["symbol_label"] for _, r in items if r.get("off_list")],
            "ml_low_price_coins": [r["symbol_label"] for _, r in items if r.get("ml_low_price")],
            "excluded_coins": [r["symbol_label"] for _, r in items if r.get("metrics") and r.get("ml_low_price")],
        })
    leaders = _score_aggregate(agg_rows)
    return {"rows": agg_rows, "leaders": leaders}


def _score_aggregate(rows: list[dict]) -> list[dict]:
    ok = [r for r in rows if r.get("metrics")]
    for r in rows:
        r["badges"] = []
        r["weak"] = []
        r["good_extra"] = []
        r["reliable"] = []
        r["one_coin"] = []
        r["score"] = 0
    leaders = []
    for key, label, higher, need_profit, good, bad in AGG_CATEGORIES:
        elig = []
        for r in ok:
            a = r["metrics"]
            if a["total_trades"] < MIN_TRADES:
                continue
            if key == "return_std" and a["coins_run"] < 2:
                continue
            if need_profit and (a["total_net"] or 0) <= 0:
                continue
            if key == "long_net" and not a["long_trades"]:
                continue
            if key == "short_net" and not a["short_trades"]:
                continue
            v = a.get(key)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            elig.append((v, r))
        if not elig:
            leaders.append({"key": key, "label": label, "top": []})
            continue
        elig.sort(key=lambda x: (x[0] if higher else -x[0], x[1]["metrics"]["total_net"]), reverse=True)
        top = elig[:3]
        for rank, (v, r) in enumerate(top, start=1):
            r["badges"].append({"key": key, "label": label, "rank": rank, "text": good})
        leaders.append({"key": key, "label": label, "top": [{"key": r["key"], "rank": i + 1, "value": v} for i, (v, r) in enumerate(top)]})
        if bad and len(elig) >= 6:
            for v, r in elig[-3:]:
                if all(r is not t[1] for t in top):
                    r["weak"].append(bad)
    for r in rows:
        a = r.get("metrics")
        if r.get("failed"):
            r["weak"].append("Failed on " + ", ".join(f["symbol_label"] for f in r["failed"]))
        if not a:
            continue
        n = a["coins_run"]
        if a["total_trades"] == 0:
            r["weak"].append("No trades on any coin")
        elif a["total_trades"] < MIN_TRADES:
            r["weak"].append(f"Only {a['total_trades']} trade(s) across all coins: too few to judge")
        if a["profitable"] <= n / 2:
            r["weak"].append(f"Profitable on only {a['profitable']} of {n} coins")
        if (a["total_net"] or 0) < 0:
            r["weak"].append("Loses money in total across the coins")
        if r["venue"] == "pivex" and a["pivex_dd_breaches"]:
            r["weak"].append(f"Hit Pivex's $600 drawdown on {a['pivex_dd_breaches']} of {n} coins")
        if r.get("ml_low_price_coins"):
            r["weak"].append(ML_LOW_PRICE_NOTE + " Left out of the cross-coin numbers: " + ", ".join(r["ml_low_price_coins"]))
        if a["total_trades"] >= MIN_TRADES:
            if n and a["profitable"] == n:
                r["good_extra"].append(f"Profitable on all {n} coins")
            elif a["profitable"] > n / 2:
                r["good_extra"].append(f"Profitable on {a['profitable']} of {n} coins ({', '.join(a['profitable_coins'])})")
            if a["median_pf"] is not None and a["median_pf"] >= 1.5:
                r["good_extra"].append(f"Median profit factor {a['median_pf']:.2f}")
            if a["median_win_rate"] is not None and a["median_win_rate"] >= 60:
                r["good_extra"].append(f"Median win rate {a['median_win_rate']:.0f}%")
            if (a["total_net"] or 0) > 0 and a["worst_dd_pct"] is not None and a["worst_dd_pct"] < 10:
                r["good_extra"].append(f"Max drawdown under 10% on every coin (worst {a['worst_dd_pct']:.1f}%)")
        if r["venue"] == "pivex" and a["pivex_target_hits"]:
            r["good_extra"].append(f"Reached +$1,000 without a $600 drawdown on {a['pivex_target_hits']} of {n} coins")
        need = max(2, math.ceil(n / 2))
        for ck, coins in sorted(r["badge_coins"].items(), key=lambda kv: -len(kv[1])):
            label = CATEGORY_LABELS.get(ck, ck)
            if len(coins) >= need:
                r["reliable"].append(f"{label}: top 3 on {len(coins)} of {n} coins ({', '.join(coins)})")
            elif len(coins) == 1 and n > 1:
                r["one_coin"].append(f"{label}: only on {coins[0]}")
        r["score"] = sum(4 - b["rank"] for b in r["badges"])
    return leaders


def run_strengths_multi(
    symbols: list[str],
    period: str,
    balance: float,
    commission: float,
    slippage: float,
    daily_filter: bool,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    all_coins: bool = True,
    isolate: bool | None = None,
    min_symbols: int = MIN_SYMBOLS,
    on_symbol_done: Callable[[dict], None] | None = None,
) -> dict:
    """Every strategy × venue on each coin in turn, then a cross-coin table."""
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) < min_symbols:
        raise ValueError(f"Pick at least {min_symbols} coins (got {len(symbols)})")
    for sym in symbols:
        if sym not in SYMBOLS:
            raise ValueError(f"Unknown symbol {sym}")
    if period not in PERIODS:
        raise ValueError(f"Unknown period {period}")
    t0 = time.monotonic()
    window = period_window(period)
    _, start, end = window
    plans = [strengths_plan(sym, all_coins=all_coins)[0] for sym in symbols]
    grand = sum(len(p) for p in plans)
    clear_memo()
    gc.collect()
    done_before = 0
    reports = []
    for idx, sym in enumerate(symbols):
        offset = done_before

        def _cb(i: int, tot: int, label: str, _off=offset, _idx=idx) -> None:
            if progress_cb:
                progress_cb(_off + i, grand, f"Coin {_idx + 1}/{len(symbols)} · {label}")

        rep = run_strengths(sym, period, balance, commission, slippage, daily_filter,
                            progress_cb=_cb, cancel_check=cancel_check, all_coins=all_coins,
                            isolate=isolate, window=window)
        reports.append(rep)
        done_before += len(plans[idx])
        if on_symbol_done:
            on_symbol_done(rep)
        clear_memo()
        gc.collect()
    if progress_cb:
        progress_cb(grand, grand, "Aggregating across coins…")
    agg = _clean(aggregate_reports(reports))
    stamp = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")  # noqa: E731
    return {
        "multi": True,
        "symbols": symbols,
        "symbol_labels": [SYMBOLS[s]["label"] for s in symbols],
        "symbol_label": ", ".join(SYMBOLS[s]["label"] for s in symbols),
        "period": period,
        "period_label": PERIODS[period]["label"],
        "window_label": f"{stamp(start)} → {stamp(end)} UTC",
        "all_coins": all_coins,
        "settings": reports[0]["settings"] if reports else {},
        "venue_rules": VENUE_RULES,
        "categories": reports[0]["categories"] if reports else [],
        "agg_categories": [{"key": c[0], "label": c[1], "higher_is_better": c[2], "needs_profit": c[3]} for c in AGG_CATEGORIES],
        "min_trades": MIN_TRADES,
        "pf_cap": PF_CAP,
        "reports": reports,
        "aggregate": agg,
        "rows_total": grand,
        "elapsed_s": round(time.monotonic() - t0, 1),
    }


def _clean(obj):
    """JSON-safe: non-finite floats become None."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


CSV_COLUMNS = [
    ("Venue", lambda r, m: r["venue_label"]),
    ("Strategy", lambda r, m: r["strategy"]),
    ("Timeframe", lambda r, m: r["timeframe"]),
    ("Symbol", lambda r, m: r["symbol"]),
    ("Score", lambda r, m: r.get("score", "")),
    ("Net profit $", lambda r, m: m.get("net_profit")),
    ("Return %", lambda r, m: m.get("return_pct")),
    ("Profit factor", lambda r, m: "inf" if m.get("pf_infinite") else m.get("profit_factor")),
    ("Win rate %", lambda r, m: m.get("win_rate")),
    ("Max DD %", lambda r, m: m.get("max_dd_pct")),
    ("Max DD $", lambda r, m: m.get("max_dd")),
    ("Recovery factor", lambda r, m: m.get("recovery_factor")),
    ("R:R", lambda r, m: m.get("rr")),
    ("Expectancy $", lambda r, m: m.get("expectancy")),
    ("Trades", lambda r, m: m.get("trades")),
    ("Max losing streak", lambda r, m: m.get("max_loss_streak")),
    ("Max winning streak", lambda r, m: m.get("max_win_streak")),
    ("Green buckets %", lambda r, m: m.get("green_pct")),
    ("Bucket", lambda r, m: m.get("bucket")),
    ("Bucket P&L std $", lambda r, m: m.get("period_std")),
    ("Sharpe (daily)", lambda r, m: m.get("sharpe")),
    ("Sortino (daily)", lambda r, m: m.get("sortino")),
    ("Avg hold h", lambda r, m: m.get("avg_hold_h")),
    ("Long trades", lambda r, m: m.get("long_trades")),
    ("Long net $", lambda r, m: m.get("long_net")),
    ("Long win %", lambda r, m: m.get("long_win_rate")),
    ("Short trades", lambda r, m: m.get("short_trades")),
    ("Short net $", lambda r, m: m.get("short_net")),
    ("Short win %", lambda r, m: m.get("short_win_rate")),
    ("Best trade $", lambda r, m: m.get("best_trade")),
    ("Worst trade $", lambda r, m: m.get("worst_trade")),
    ("Time in market %", lambda r, m: m.get("time_in_market_pct")),
    ("Fees $", lambda r, m: m.get("fees")),
    ("Fee drag %", lambda r, m: m.get("fee_drag_pct")),
    ("Worst day $ (Kyiv)", lambda r, m: m.get("worst_day")),
    ("Pivex $600/$1,000", lambda r, m: m.get("pivex_info", "")),
    ("Good for", lambda r, m: "; ".join([b["text"] + f" (#{b['rank']})" for b in r.get("badges", [])] + r.get("good_extra", []))),
    ("Weaknesses", lambda r, m: "; ".join(r.get("weak", []))),
    ("Error", lambda r, m: r.get("error") or ""),
]


def to_csv(report: dict) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[0] for c in CSV_COLUMNS])
    for r in report["rows"]:
        m = r.get("metrics") or {}
        out = []
        for _, fn in CSV_COLUMNS:
            v = fn(r, m)
            out.append(f"{v:.4f}" if isinstance(v, float) else ("" if v is None else v))
        w.writerow(out)
    for r in report["skipped"]:
        w.writerow([r["venue_label"], r["strategy"], r["timeframe"], r["symbol"]] + [""] * (len(CSV_COLUMNS) - 5) + [r["note"]])
    return buf.getvalue()


AGG_CSV_COLUMNS = [
    ("Venue", lambda r, a: r["venue_label"]),
    ("Strategy", lambda r, a: r["strategy"]),
    ("Timeframe", lambda r, a: r["timeframe"]),
    ("Score (cross-coin)", lambda r, a: r.get("score", "")),
    ("Coins run", lambda r, a: a.get("coins_run")),
    ("Profitable coins", lambda r, a: a.get("profitable")),
    ("Profitable on", lambda r, a: ", ".join(a.get("profitable_coins") or [])),
    ("Total net $", lambda r, a: a.get("total_net")),
    ("Median return %", lambda r, a: a.get("median_return_pct")),
    ("Worst coin return %", lambda r, a: a.get("worst_return_pct")),
    ("Return std across coins %", lambda r, a: a.get("return_std")),
    ("Median PF", lambda r, a: a.get("median_pf")),
    ("Mean PF", lambda r, a: a.get("mean_pf")),
    ("Median win %", lambda r, a: a.get("median_win_rate")),
    ("Median max DD %", lambda r, a: a.get("median_dd_pct")),
    ("Worst max DD %", lambda r, a: a.get("worst_dd_pct")),
    ("Worst DD coin", lambda r, a: a.get("worst_dd_coin")),
    ("Avg green months %", lambda r, a: a.get("avg_green_pct")),
    ("Median recovery", lambda r, a: a.get("median_recovery")),
    ("Median R:R", lambda r, a: a.get("median_rr")),
    ("Median expectancy $", lambda r, a: a.get("median_expectancy")),
    ("Median Sharpe", lambda r, a: a.get("median_sharpe")),
    ("Median Sortino", lambda r, a: a.get("median_sortino")),
    ("Worst losing streak", lambda r, a: a.get("max_loss_streak")),
    ("Total trades", lambda r, a: a.get("total_trades")),
    ("Long net $", lambda r, a: a.get("long_net")),
    ("Short net $", lambda r, a: a.get("short_net")),
    ("Worst trade $", lambda r, a: a.get("worst_trade")),
    ("Worst day $", lambda r, a: a.get("worst_day")),
    ("Median fee drag %", lambda r, a: a.get("median_fee_drag")),
    ("Median time in market %", lambda r, a: a.get("median_time_in_market")),
    ("Median hold h", lambda r, a: a.get("median_hold_h")),
    ("Pivex $600 DD hit on", lambda r, a: a.get("pivex_dd_breaches") if r["venue"] == "pivex" else ""),
    ("Good for", lambda r, a: "; ".join([b["text"] + f" (#{b['rank']})" for b in r.get("badges", [])] + r.get("good_extra", []))),
    ("Reliable across coins", lambda r, a: "; ".join(r.get("reliable", []))),
    ("Only on one coin", lambda r, a: "; ".join(r.get("one_coin", []))),
    ("Weaknesses", lambda r, a: "; ".join(r.get("weak", []))),
    ("Failed", lambda r, a: "; ".join(f"{f['symbol_label']}: {f['error']}" for f in r.get("failed", []))),
]


def to_csv_multi(report: dict) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"Cross-coin table · {report['symbol_label']} · {report['period_label']} · {report['window_label']}"])
    w.writerow([c[0] for c in AGG_CSV_COLUMNS])
    for r in report["aggregate"]["rows"]:
        a = r.get("metrics") or {}
        out = []
        for _, fn in AGG_CSV_COLUMNS:
            v = fn(r, a)
            out.append(f"{v:.4f}" if isinstance(v, float) else ("" if v is None else v))
        w.writerow(out)
    w.writerow([])
    w.writerow(["Per-coin rows"])
    first = True
    for rep in report["reports"]:
        text = to_csv(rep)
        lines = text.splitlines(True)
        buf.write("".join(lines if first else lines[1:]))
        first = False
    return buf.getvalue()

