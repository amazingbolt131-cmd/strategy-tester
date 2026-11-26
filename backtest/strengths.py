"""Strengths run: every strategy on both venues, one coin, one period, ranked together.

Each strategy runs under its own venue's rules (Pivex single-entry, k-scaled size and
the daily HAMA filter setting; Binance multi-entry), with the commission and slippage
from the form. Rows are then scored across categories so each strategy x timeframe x
venue gets a "Good for" list and a weaknesses list.
"""

from __future__ import annotations

import gc
import math
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
from .data import period_window
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


def strengths_plan(symbol: str) -> tuple[list[dict], list[dict]]:
    """(rows to run, rows skipped because the coin is not offered)."""
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


def run_strengths(
    symbol: str,
    period: str,
    balance: float,
    commission: float,
    slippage: float,
    daily_filter: bool,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown symbol {symbol}")
    if period not in PERIODS:
        raise ValueError(f"Unknown period {period}")
    t0 = time.monotonic()
    window = period_window(period)
    _, start, end = window
    rows, skipped = strengths_plan(symbol)
    total = len(rows)
    for i, row in enumerate(rows):
        if cancel_check and cancel_check():
            raise RuntimeError("Strengths run cancelled by a newer request")
        if progress_cb:
            progress_cb(i, total, f"{row['venue_label']} · {row['strategy']} · {row['symbol_label']}")
        t1 = time.monotonic()
        try:
            res = run_backtest(
                row["strategy_id"], symbol, balance, commission, slippage,
                window=window, venue=row["venue"], k=DEFAULT_K, daily_filter=daily_filter,
                include_chart=False, with_trades=True,
            )
            row["metrics"] = row_metrics(res, balance, start, end)
            row["note"] = res.get("note", "")
            row["error"] = None
        except Exception as exc:  # noqa: BLE001 - one failed row must not stop the run
            row["metrics"] = None
            msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else exc.__class__.__name__
            if row["strategy_id"] in HEAVY_1M and period in ("6m", "1y"):
                msg += " · This strategy reads 1m candles; a 6- or 12-month run can exceed the free server's memory or time. Try a shorter period."
            row["error"] = msg[:600]
        row["elapsed_s"] = round(time.monotonic() - t1, 1)
        gc.collect()
    leaders = score_rows(rows)
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
