"""Precomputed HAMA Scale-in Ladder, 1h Alpha Wave Scalper + HAMA, and DG Grid + flow.

Results are bundled for the 1-year (2025-11-27 → 2026-11-27) and 3-year
(2023-12-01 → now) windows on BTC, ETH, SOL, BNB and XRP. Fees of 0.04%/side
are already in net P&L. Totals are the sum of per-trade % of a $100 unit
(1% = $1). Max DD is the peak-to-trough of that cumulative %.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import STRATEGY_BY_ID, SYMBOLS, VENUE_LABELS

STRATEGY_IDS = ("hama_ladder_1m", "aw_scalper_1h", "dg_grid_flow")
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "research_strats.json"
UNIT_USD = 100.0
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

_DATA: dict | None = None


def load() -> dict:
    global _DATA
    if _DATA is None:
        with DATA_PATH.open() as fh:
            _DATA = json.load(fh)
    return _DATA


def is_research(strategy_id: str) -> bool:
    return strategy_id in STRATEGY_IDS


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _stats_to_app(st: dict) -> dict:
    n = int(st.get("n") or 0)
    tot = float(st.get("tot") or 0.0)
    gp = float(st.get("gross_profit") or 0.0)
    gl = float(st.get("gross_loss") or 0.0)
    wins = n * (float(st.get("win") or 0.0) / 100.0) if n else 0.0
    win_n = int(round(wins))
    loss_n = n - win_n
    avg = float(st.get("avg") or 0.0)
    avg_win = (gp / win_n) if win_n else 0.0
    avg_loss = (gl / loss_n) if loss_n else 0.0
    ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    dd = float(st.get("dd") or 0.0)
    return {
        "net_profit": tot,
        "net_profit_pct": tot,
        "pnl_pct": tot,
        "ending_equity": UNIT_USD + tot,
        "gross_profit": gp,
        "gross_loss": gl,
        "profit_factor": st.get("pf"),
        "max_drawdown": dd,
        "max_drawdown_pct": dd,
        "return_dd_ratio": (tot / dd) if dd > 0 else None,
        "total_trades": n,
        "wins": win_n,
        "losses": loss_n,
        "win_rate": float(st.get("win") or 0.0),
        "avg_trade": avg,
        "expectancy": avg,
        "expectancy_pct": avg,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": ratio,
        "largest_win": 0.0,
        "largest_loss": 0.0,
        "max_consec_wins": 0,
        "max_consec_losses": 0,
        "sharpe": None,
        "commission_paid": 0.0,
        "starting_balance": UNIT_USD,
        "long_trades": 0,
        "long_wins": 0,
        "long_win_rate": 0.0,
        "long_net_profit": 0.0,
        "short_trades": 0,
        "short_wins": 0,
        "short_win_rate": 0.0,
        "short_net_profit": 0.0,
        "avg_run_up": 0.0,
        "avg_drawdown": 0.0,
        "time_in_market_pct": None,
        "avg_duration_hours": 0.0,
        "exit_reasons": {},
        "months": st.get("months") or {},
    }


def _window(period: str) -> dict:
    data = load()
    if period not in data["windows"]:
        raise ValueError(
            f"{period} is not a precomputed window. "
            "These three strategies are precomputed for 1 year and 3 years only."
        )
    return data["windows"][period]


def run_precomputed(
    strategy_id: str,
    symbol: str,
    period: str,
    balance: float = 100.0,
    include_chart: bool = True,
    with_trades: bool = False,
) -> dict:
    if strategy_id not in STRATEGY_IDS:
        raise ValueError(f"Unknown research strategy {strategy_id}")
    if period not in ("1y", "3y"):
        raise ValueError(
            "HAMA Scale-in Ladder, 1h Alpha Wave Scalper + HAMA, and DG Grid + flow "
            "are precomputed for the 1-year and 3-year periods only."
        )
    if symbol not in COINS:
        raise ValueError(f"{symbol} is not in the 5-coin set (BTC ETH SOL BNB XRP)")
    data = load()
    win = _window(period)
    meta = data["strategies"][strategy_id]
    cfg = STRATEGY_BY_ID[strategy_id]
    block = win["strategies"][strategy_id]["per_symbol"][symbol]
    st = _stats_to_app(block["stats"])
    # Fill a few trade-derived fields from the list when present
    raw_trades = block.get("trades") or []
    if raw_trades:
        pnls = [float(t["net"]) for t in raw_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        st["largest_win"] = max(wins) if wins else 0.0
        st["largest_loss"] = min(losses) if losses else 0.0
        longs = [t for t in raw_trades if int(t["side"]) == 1]
        shorts = [t for t in raw_trades if int(t["side"]) == -1]
        st["long_trades"] = len(longs)
        st["short_trades"] = len(shorts)
        st["long_net_profit"] = float(sum(t["net"] for t in longs))
        st["short_net_profit"] = float(sum(t["net"] for t in shorts))
        st["long_wins"] = sum(1 for t in longs if t["net"] > 0)
        st["short_wins"] = sum(1 for t in shorts if t["net"] > 0)
        st["long_win_rate"] = (st["long_wins"] / len(longs) * 100.0) if longs else 0.0
        st["short_win_rate"] = (st["short_wins"] / len(shorts) * 100.0) if shorts else 0.0

    note = data["note"] + " " + win["window_label"] + "."
    trades_out = []
    equity = [{"time": int(win["start_ms"] // 1000), "value": round(float(balance), 2)}]
    drawdown = [{"time": int(win["start_ms"] // 1000), "value": 0.0}]
    cum = 0.0
    peak = float(balance)
    last_stamp = int(win["start_ms"] // 1000)
    ordered = sorted(raw_trades, key=lambda t: (int(t["t_exit"]), int(t["t_entry"])))
    for i, t in enumerate(ordered, start=1):
        pnl = float(t["net"])
        cum += pnl
        eq = float(balance) + cum
        entry_px = t.get("entry")
        exit_px = t.get("exit")
        notional = UNIT_USD
        trades_out.append({
            "n": i,
            "side": "Long" if int(t["side"]) == 1 else "Short",
            "entry_time": _iso(int(t["t_entry"])),
            "entry_price": entry_px if entry_px is not None else 0.0,
            "exit_time": _iso(int(t["t_exit"])),
            "exit_price": exit_px if exit_px is not None else 0.0,
            "reason": "research → close",
            "qty": 1.0,
            "pnl": pnl,
            "pnl_pct": pnl,
            "cumulative": cum,
            "run_up": 0.0,
            "drawdown": 0.0,
            "entry_ms": int(t["t_entry"]),
            "exit_ms": int(t["t_exit"]),
            "commission": 0.0,
        })
        stamp = int(int(t["t_exit"]) // 1000)
        if stamp <= last_stamp:
            stamp = last_stamp + 1
        last_stamp = stamp
        peak = max(peak, eq)
        equity.append({"time": stamp, "value": round(eq, 2)})
        dd = 0.0 if not peak else (eq - peak) / peak * 100.0
        drawdown.append({"time": stamp, "value": round(dd, 3)})

    months = [
        {"month": mk, "pnl": v["pnl"], "trades": v["n"], "profit": v["pnl"] > 0}
        for mk, v in (block["stats"].get("months") or {}).items()
    ]
    result = {
        "strategy_id": strategy_id,
        "strategy": cfg["label"],
        "venue": "binance",
        "venue_label": VENUE_LABELS["binance"],
        "symbol": symbol,
        "symbol_label": SYMBOLS[symbol]["label"],
        "rules": list(cfg.get("rules") or meta.get("rules") or []) + [note],
        "note": note,
        "k": 1.0,
        "daily_filter": False,
        "stats": st,
        "months": months,
        "trades": trades_out if (include_chart or with_trades) else (
            [{"side": 1 if t["side"] == "Long" else -1, "entry_ms": t["entry_ms"],
              "exit_ms": t["exit_ms"], "pnl": t["pnl"], "commission": 0.0} for t in trades_out]
            if with_trades else []
        ),
        "chart": {
            "equity": equity,
            "drawdown": drawdown,
            "candles": [],
            "markers": [],
        } if include_chart else None,
        "window_label": win["window_label"],
        "window_start": _iso(int(win["start_ms"])),
        "window_end": _iso(int(win["end_ms"])),
        "precomputed": True,
    }
    return result


def _row(strategy_id: str, period: str, symbol: str | None, combined: bool, rank: int) -> dict:
    data = load()
    win = _window(period)
    meta = data["strategies"][strategy_id]
    block = win["strategies"][strategy_id]
    if combined:
        st = _stats_to_app(block["combined"]["stats"])
        months = block["combined"]["stats"].get("months") or {}
        symbol_id = "ALL5"
        symbol_label = "5 coins combined"
        tested = 5
    else:
        st = _stats_to_app(block["per_symbol"][symbol]["stats"])
        months = block["per_symbol"][symbol]["stats"].get("months") or {}
        symbol_id = symbol
        symbol_label = SYMBOLS[symbol]["label"]
        tested = 1
    st["months"] = months
    return {
        "rank": rank,
        "venue": "binance",
        "venue_label": "Binance",
        "strategy_id": strategy_id,
        "strategy_label": meta["label"],
        "symbol": symbol_id,
        "symbol_label": symbol_label,
        "stats": st,
        "note": data["note"],
        "error": None,
        "signal_tf": meta["signal_tf"],
        "tp": meta["tp"],
        "sl": meta["sl"],
        "indicators": meta["indicators"],
        "symbols_tested": 5 if combined else tested,
        "completed_symbols": 5 if combined else tested,
        "failed_symbols": 0,
        "research": True,
        "combined": combined,
        "months": [
            {"month": mk, "pnl": v["pnl"], "trades": v["n"], "profit": v["pnl"] > 0}
            for mk, v in months.items()
        ],
    }


def research_rows(period: str) -> list[dict]:
    rows = []
    idx = 1
    for sid in STRATEGY_IDS:
        rows.append(_row(sid, period, None, True, idx))
        idx += 1
        for sym in COINS:
            rows.append(_row(sid, period, sym, False, idx))
            idx += 1
    return rows


def merge_into_ranking(report: dict, period: str = "1y") -> dict:
    """Append research rows to an existing ranking report (does not redo v3.1 ranks)."""
    report = copy.deepcopy(report)
    rows = research_rows(period)
    combined = [r for r in rows if r.get("combined")]
    per_coin = [r for r in rows if not r.get("combined")]
    existing_ids = {(r.get("strategy_id"), r.get("venue"), r.get("symbol")) for r in report.get("ranked") or []}
    for row in combined:
        if (row["strategy_id"], row["venue"], row["symbol"]) not in existing_ids:
            report.setdefault("ranked", []).append(row)
    existing_all = {(r.get("strategy_id"), r.get("venue"), r.get("symbol")) for r in report.get("all_results") or []}
    for row in combined + per_coin:
        if (row["strategy_id"], row["venue"], row["symbol"]) not in existing_all:
            report.setdefault("all_results", []).append(row)
    report["research_period"] = period
    win = _window(period)
    report["research_window_label"] = win["window_label"]
    report["research_note"] = load()["note"]
    return report


def research_ranking_report(period: str) -> dict:
    rows = research_rows(period)
    combined = [r for r in rows if r.get("combined")]
    # rank combined by net profit
    combined = sorted(combined, key=lambda r: -float((r["stats"] or {}).get("net_profit") or 0))
    for i, row in enumerate(combined, start=1):
        row["rank"] = i
    win = _window(period)
    data = load()
    return {
        "window_label": win["window_label"],
        "source": "precomputed",
        "source_label": (
            f"Precomputed · {win['label']} {win['window_label']} · "
            "$100 unit, 0.04%/side already in net · 5 coins BTC ETH SOL BNB XRP"
        ),
        "settings": {
            "balance": UNIT_USD,
            "commission": 0.04,
            "slippage": 0.0,
            "daily_filter": False,
            "internal_k": 1,
            "native_timeframes": "Each research strategy uses its own native timeframe (1m / 1h / 15m).",
        },
        "universe": {
            "binance_symbols": list(COINS),
            "binance_source": "Research strategies tested on BTC, ETH, SOL, BNB and XRP only.",
            "pivex_symbols": [],
            "pivex_note": "These three strategies are Binance USDT-M research engines, not Pivex cards.",
        },
        "summary": {
            "total_combos": len(rows),
            "completed_combos": len(rows),
            "failed_combos": 0,
            "strategies_ranked": len(combined),
            "elapsed_s": 0.0,
        },
        "ranked": combined,
        "all_results": rows,
        "research_period": period,
        "research_window_label": win["window_label"],
        "research_note": data["note"],
        "morning_hama": None,
    }
