"""Rank each enabled strategy by its best symbol on the default 30-day window.

Each strategy is run only on its own native signal and execution timeframes
from the v3.1 engine. Timeframes are not swept or overridden.
"""

from __future__ import annotations

import gc
import re
import time
from typing import Callable

from .config import (
    DEFAULT_K,
    STRATEGIES,
    SYMBOLS,
    VENUE_LABELS,
    venue_enabled,
)
from .data import test_window
from .runner import run_backtest
from .sweep import STRATEGY_SPECS

# Frozen 2026-11-25 snapshot: 38 most liquid Binance USDT-M *crypto* perps
# by 24h quoteVolume from fapi.binance.com /fapi/v1/ticker/24hr, keeping only
# TRADING PERPETUAL contracts with quote and margin asset USDT and
# underlyingType=COIN (TradFi equities, commodities, and indices excluded).
# PAXGUSDT and XAUTUSDT are included because Binance classifies them as COIN (RWA).
BINANCE_RANKING_SOURCE = (
    "38 most liquid Binance USDT-M crypto perpetual contracts by 24h USDT volume "
    "on 2026-11-25 (fapi.binance.com ticker/24hr + exchangeInfo). "
    "TradFi, commodity, and index contracts were excluded. "
    "PAXG and XAUT are included as Binance COIN/RWA contracts."
)
BINANCE_RANKING_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "ZECUSDT",
    "HYPEUSDT",
    "XRPUSDT",
    "BIGTIMEUSDT",
    "1000PEPEUSDT",
    "EVAAUSDT",
    "BNBUSDT",
    "NEARUSDT",
    "DOGEUSDT",
    "LISTAUSDT",
    "BLESSUSDT",
    "GUAUSDT",
    "MMTUSDT",
    "PAXGUSDT",
    "MANTRAUSDT",
    "UNIUSDT",
    "ADAUSDT",
    "ENAUSDT",
    "SUIUSDT",
    "LINKUSDT",
    "PIEVERSEUSDT",
    "OPGUSDT",
    "WLDUSDT",
    "BTWUSDT",
    "XAUTUSDT",
    "TAOUSDT",
    "AAVEUSDT",
    "AVAXUSDT",
    "AIOTUSDT",
    "PUMPUSDT",
    "VVVUSDT",
    "AKEUSDT",
    "LTCUSDT",
    "INJUSDT",
    "FILUSDT",
]

# Verified listed Pivex crypto coins. A separate login check may replace this
# with the full board; until then do not invent additional Pivex listings.
# ZEC and UNI remain unconfirmed.
PIVEX_RANKING_NOTE = (
    "Known listed Pivex crypto coins, pending a separate login check of the full board: "
    "BTC, ETH, XRP, LTC, DOGE, SOL. ZEC and UNI remain unconfirmed."
)
PIVEX_RANKING_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "LTCUSDT",
    "DOGEUSDT",
    "SOLUSDT",
]

NATIVE_TF_NOTE = (
    "Each strategy is run only on its own native signal and execution timeframes "
    "from the v3.1 engine config. Timeframes are not swept."
)

# Split existing tp_sl sentences without rewriting any numbers or rules.
_TP_MARK = re.compile(r"\bTP\d*\b|\bno (?:fixed )?TP\b|\bno TP\b", re.I)
_SL_MARK = re.compile(r"\bSL\b|\btime stop\b", re.I)


def split_tp_sl(text: str) -> tuple[str, str]:
    """Split already-written TP/SL text into two columns. Do not invent wording."""
    raw = (text or "").strip()
    if not raw:
        return "—", "—"
    chunks = [part.strip() for part in re.split(r"(?<=[.;])\s+", raw) if part.strip()]
    tp_parts: list[str] = []
    sl_parts: list[str] = []
    other: list[str] = []
    for chunk in chunks:
        is_tp = bool(_TP_MARK.search(chunk))
        is_sl = bool(_SL_MARK.search(chunk))
        if is_tp:
            tp_parts.append(chunk)
        if is_sl:
            sl_parts.append(chunk)
        if not is_tp and not is_sl:
            other.append(chunk)
    context = " ".join(other)
    tp = " ".join(tp_parts) if tp_parts else "—"
    sl = " ".join(sl_parts) if sl_parts else "—"
    if context and tp_parts:
        tp = f"{context} {tp}"
    elif context and not tp_parts:
        tp = context
    if context and sl_parts:
        sl = f"{context} {sl}"
    elif context and not sl_parts:
        sl = context
    return tp, sl


def strategy_rule_fields(strategy_id: str) -> dict[str, str]:
    """TP, SL, indicator and timeframe text from existing strategy metadata only."""
    spec = STRATEGY_SPECS.get(strategy_id, {})
    tp, sl = split_tp_sl(str(spec.get("tp_sl") or ""))
    indicators = str(spec.get("indicators") or "").strip() or "—"
    signal_tf = str(spec.get("signal_tf") or "").strip() or "—"
    execution_tf = str(spec.get("execution_tf") or "").strip() or "—"
    return {
        "tp": tp,
        "sl": sl,
        "indicators": indicators,
        "signal_tf": signal_tf,
        "execution_tf": execution_tf,
    }


def ranking_universe(venue: str) -> list[str]:
    if venue == "binance":
        symbols = BINANCE_RANKING_SYMBOLS
    elif venue == "pivex":
        symbols = PIVEX_RANKING_SYMBOLS
    else:
        raise ValueError(f"Unknown venue {venue}")
    missing = [symbol for symbol in symbols if symbol not in SYMBOLS]
    if missing:
        raise ValueError(f"Ranking symbols missing from SYMBOLS: {', '.join(missing)}")
    return list(symbols)


def ranking_plan() -> tuple[list[dict], list[dict]]:
    """Return (canonical_combos, execution_order grouped by symbol)."""
    canonical: list[dict] = []
    idx = 0
    for venue, symbols in (
        ("pivex", ranking_universe("pivex")),
        ("binance", ranking_universe("binance")),
    ):
        for strategy in STRATEGIES:
            if not venue_enabled(strategy, venue):
                continue
            for symbol in symbols:
                canonical.append(
                    {
                        "order_idx": idx,
                        "venue": venue,
                        "venue_label": VENUE_LABELS[venue],
                        "strategy_id": strategy["id"],
                        "strategy_label": strategy["label"],
                        "symbol": symbol,
                        "symbol_label": SYMBOLS[symbol]["label"],
                    }
                )
                idx += 1
    priority = {"BTCUSDT": 0, "SOLUSDT": 1}
    exec_order = sorted(
        canonical,
        key=lambda item: (priority.get(item["symbol"], 2), item["symbol"], item["order_idx"]),
    )
    return canonical, exec_order


def _pf_sort_value(row: dict) -> float:
    if row.get("error") or not row.get("stats"):
        return float("-inf")
    pf = row["stats"]["profit_factor"]
    if pf is None:
        return float("inf")
    return float(pf)


def rank_rows(rows: list[dict]) -> list[dict]:
    """Default order: profit factor descending, then net return descending. Errors last."""
    ordered = sorted(
        rows,
        key=lambda row: (
            1 if row.get("error") or not row.get("stats") else 0,
            -_pf_sort_value(row),
            -(row["stats"]["net_profit"] if row.get("stats") else -1e18),
        ),
    )
    for index, row in enumerate(ordered, start=1):
        row["rank"] = index
    return ordered


def _best_key(row: dict) -> tuple:
    """Best symbol: highest net profit, then PF, then trade count. Errors last."""
    if row.get("error") or not row.get("stats"):
        return (1, 0.0, 0.0, 0)
    stats = row["stats"]
    pf = _pf_sort_value(row)
    pf_key = 1e18 if pf == float("inf") else pf
    return (0, -float(stats["net_profit"]), -pf_key, -int(stats["total_trades"]))


def best_per_strategy(rows: list[dict]) -> list[dict]:
    """One row per venue+strategy: the symbol with the best net profit."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["venue"], row["strategy_id"]), []).append(row)
    winners: list[dict] = []
    for items in groups.values():
        ok = [row for row in items if not row.get("error") and row.get("stats")]
        pool = ok if ok else items
        winner = dict(sorted(pool, key=_best_key)[0])
        winner["symbols_tested"] = len(items)
        winner["completed_symbols"] = len(ok)
        winner["failed_symbols"] = sum(1 for row in items if row.get("error"))
        winners.append(winner)
    return rank_rows(winners)


def build_ranking(
    rows: list[dict],
    window_label: str,
    balance: float,
    commission: float,
    slippage: float,
    daily_filter: bool,
    elapsed_s: float,
) -> dict:
    ranked = best_per_strategy(rows)
    ok_rows = [row for row in rows if not row.get("error") and row.get("stats")]
    return {
        "window_label": window_label,
        "settings": {
            "balance": balance,
            "commission": commission,
            "slippage": slippage,
            "daily_filter": daily_filter,
            "internal_k": DEFAULT_K,
            "native_timeframes": NATIVE_TF_NOTE,
        },
        "universe": {
            "binance_symbols": list(BINANCE_RANKING_SYMBOLS),
            "binance_source": BINANCE_RANKING_SOURCE,
            "pivex_symbols": list(PIVEX_RANKING_SYMBOLS),
            "pivex_note": PIVEX_RANKING_NOTE,
        },
        "summary": {
            "total_combos": len(rows),
            "completed_combos": len(ok_rows),
            "failed_combos": sum(1 for row in rows if row.get("error")),
            "strategies_ranked": len(ranked),
            "elapsed_s": elapsed_s,
        },
        "ranked": ranked,
        "all_results": rows,
    }


def run_ranking(
    balance: float,
    commission: float,
    slippage: float,
    daily_filter: bool,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Run every enabled strategy on the ranking universes, native timeframes only."""
    t0 = time.monotonic()
    canonical, exec_order = ranking_plan()
    total = len(exec_order)
    if total == 0:
        raise ValueError("No enabled strategy/symbol combos to rank")
    window = test_window()
    window_label = ""
    results_by_idx: dict[int, dict] = {}

    for done_idx, item in enumerate(exec_order):
        if cancel_check and cancel_check():
            raise RuntimeError("Ranking cancelled by a newer request")
        label = f"{item['venue_label']} · {item['strategy_label']} · {item['symbol_label']}"
        if progress_cb:
            progress_cb(done_idx, total, label)
        rules = strategy_rule_fields(item["strategy_id"])
        try:
            # Native timeframes only: run_backtest uses the engine's own series
            # (no timeframe argument, no TF sweep).
            result = run_backtest(
                item["strategy_id"],
                item["symbol"],
                balance,
                commission,
                slippage,
                window=window,
                venue=item["venue"],
                k=DEFAULT_K,
                daily_filter=daily_filter,
                include_chart=False,
            )
            if not window_label:
                window_label = result["window_label"]
            results_by_idx[item["order_idx"]] = {
                **item,
                "stats": result["stats"],
                "note": result.get("note", ""),
                "error": None,
                **rules,
            }
        except Exception as exc:  # noqa: BLE001 - surface the failure in the table
            results_by_idx[item["order_idx"]] = {
                **item,
                "stats": None,
                "note": "",
                "error": str(exc),
                **rules,
            }
        if (done_idx + 1) % 10 == 0:
            gc.collect()

    if progress_cb:
        progress_cb(total, total, "Building ranking…")

    ordered_rows = [results_by_idx[item["order_idx"]] for item in canonical]
    return build_ranking(
        ordered_rows,
        window_label=window_label or "Last 30 published days",
        balance=balance,
        commission=commission,
        slippage=slippage,
        daily_filter=daily_filter,
        elapsed_s=time.monotonic() - t0,
    )
