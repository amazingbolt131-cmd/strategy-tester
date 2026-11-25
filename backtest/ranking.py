"""Rank each enabled strategy by its best symbol on the default 30-day window.

Each strategy is run only on its own native signal and execution timeframes
from the v3.1 engine. Timeframes are not swept or overridden.
"""

from __future__ import annotations

import gc
import json
from pathlib import Path
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
from .data import RANKING_WINDOW_LABEL, ranking_window
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

MORNING_HAMA_TEST_TARGET = 1000.0
MORNING_HAMA_TEST_NOTE = (
    "SOL 1m Morning HAMA engine, native 1m, tested on every Ranking coin (6 known Pivex coins and "
    "38 Binance USDT-M perps). Official rules are unchanged: $40,000 notional, −$100 stop, $600 day bust, "
    "08:00 Kyiv day filter, no take-profit. The $0.06/unit entry spread is SOL-specific; on other symbols "
    "it is scaled to the same fraction of price as $0.06 / SOL 1m close on that bar, so SOL itself stays "
    "at $0.06. This ranking row uses the +$1,000 per-trade dollar take-profit test variant (stop is filled "
    "first if the same bar could hit both). Run and Start stay on the official no-target engine and their "
    "dropdown lists are unchanged."
)


SUSPECT_ROWS = {
    ("ml_5m", "binance", "BIGTIMEUSDT"): (
        "Tester artifact, not a real result. On a prior window every exit was labeled stop yet "
        "counted as a win, with an implausible average win versus ML's 3% stop and 3.1% target. "
        "Ignore this row unless a later inspection clears it."
    ),
}

DEFAULT_RANKING_PATH = Path(__file__).resolve().parent.parent / "data" / "ranking_default.json"
DEFAULT_RANKING_LABEL = (
    "Precomputed default · 1-year window 2025-11-25 00:00 → 2026-11-25 00:00 UTC · "
    "includes the former 2026-07-01 to 2026-10-25 holdout · $10,000 start, 0.04%/side, 0 slip, daily HAMA on · "
    "Other strategies from the local 303-combo run on 25 Nov 2026, 20:31–21:09 Europe/Kiev · "
    "Morning HAMA rerun 25 Nov 2026, 21:33–21:41 Europe/Kiev on all 6 known Pivex coins and all 38 Binance USDT coins "
    "(76 of 76 engine runs; tab rows use the +$1,000 target). 344 of 346 ranking combos completed."
)

_DEFAULT_RANKING: dict | None = None


def _ml_artifact_reason(row: dict) -> str | None:
    """UI-only flag for ML rows whose P&L cannot fit the 3%/3.1% $10k-notional rules."""
    known = SUSPECT_ROWS.get((row.get("strategy_id"), row.get("venue"), row.get("symbol")))
    if known:
        return known
    if row.get("strategy_id") != "ml_5m" or row.get("error"):
        return None
    stats = row.get("stats") or {}
    avg_win = stats.get("avg_win")
    avg_loss = stats.get("avg_loss")
    net = float(stats.get("net_profit") or 0)
    drawdown = float(stats.get("max_drawdown") or 0)
    exits = stats.get("exit_reasons") or {}
    trades = int(stats.get("total_trades") or 0)
    stop_only = bool(trades and exits.get("SL") == trades)
    implausible = (
        (avg_win is not None and avg_win > 2000)
        or (avg_loss is not None and abs(avg_loss) > 2000)
        or abs(net) > 100_000
        or drawdown > 50_000
        or (stop_only and (stats.get("win_rate") or 0) >= 99)
    )
    if not implausible:
        return None
    return (
        "Tester artifact, not a real result. ML's official rules are a 3% stop and 3.1% target "
        "on a $10,000 notional, so a typical win is about $310. This row's P&L or average win/loss "
        "is far outside that scale, and some symbols show every exit labeled a stop yet counted as a win. "
        "Ignore this row."
    )


def annotate_suspect_rows(report: dict) -> dict:
    """Mark known tester-artifact rows. Does not change engine results."""
    warnings: list[dict] = []
    for key in ("ranked", "all_results"):
        for row in report.get(key) or []:
            reason = _ml_artifact_reason(row)
            if not reason:
                continue
            row["suspect"] = True
            row["suspect_reason"] = reason
            if key == "ranked":
                warnings.append(
                    {
                        "strategy_id": row.get("strategy_id"),
                        "strategy_label": row.get("strategy_label"),
                        "venue_label": row.get("venue_label"),
                        "symbol_label": row.get("symbol_label"),
                        "reason": reason,
                    }
                )
    report["suspect_warnings"] = warnings
    return report


def default_ranking_report() -> dict:
    """Bundled finished ranking shown when no in-memory job is available."""
    global _DEFAULT_RANKING
    if _DEFAULT_RANKING is None:
        with DEFAULT_RANKING_PATH.open() as fh:
            report = json.load(fh)
        report["source"] = "precomputed"
        report["source_label"] = DEFAULT_RANKING_LABEL
        annotate_suspect_rows(report)
        _DEFAULT_RANKING = report
    return _DEFAULT_RANKING


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
            # Ranking-only: Morning HAMA is tested on this venue's full coin list,
            # including Binance, even though Run/Start still have it Pivex-only.
            ranking_morning_binance = strategy["id"] == "sol1m_morning" and venue == "binance"
            if not venue_enabled(strategy, venue) and not ranking_morning_binance:
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


def _morning_hama_section(ranked: list[dict], rows: list[dict]) -> dict:
    """Morning HAMA $1,000-target results: best symbol among all coins tested."""
    ranked_morning = [row for row in ranked if row.get("strategy_id") == "sol1m_morning"]
    matches = [row for row in rows if row.get("strategy_id") == "sol1m_morning"]
    ok = [row for row in ranked_morning if not row.get("error") and row.get("stats")]
    best = sorted(ok, key=_best_key)[0] if ok else (ranked_morning[0] if ranked_morning else None)
    stats = (best or {}).get("stats") if best and not best.get("error") else None
    return {
        "label": "SOL 1m Morning HAMA — $1,000 target test, all Ranking coins",
        "note": MORNING_HAMA_TEST_NOTE,
        "symbol": (best or {}).get("symbol"),
        "symbol_label": (best or {}).get("symbol_label"),
        "venue": (best or {}).get("venue"),
        "venue_label": (best or {}).get("venue_label"),
        "timeframe": "1m (native)",
        "profit_target": MORNING_HAMA_TEST_TARGET,
        "row": best,
        "rows": ranked_morning,
        "runs": matches,
        "stats": stats,
    }


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
    report = {
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
        "morning_hama": _morning_hama_section(ranked, rows),
    }
    return annotate_suspect_rows(report)


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
    window = ranking_window()
    window_label = RANKING_WINDOW_LABEL
    results_by_idx: dict[int, dict] = {}

    for done_idx, item in enumerate(exec_order):
        if cancel_check and cancel_check():
            raise RuntimeError("Ranking cancelled by a newer request")
        label = f"{item['venue_label']} · {item['strategy_label']} · {item['symbol_label']}"
        if progress_cb:
            progress_cb(done_idx, total, label)
        rules = strategy_rule_fields(item["strategy_id"])
        extra = {}
        if item["strategy_id"] == "sol1m_morning":
            extra["profit_target"] = MORNING_HAMA_TEST_TARGET
            rules = dict(rules)
            rules["tp"] = (
                "Test variant: +$1,000 per-trade profit target on the native 1m "
                "(official Run/Start engine has no target). "
                "Filled on the 1m bar's favorable extreme at the exact price that makes that trade's P&L = $1,000."
            )
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
                skip_missing_days=True,
                **extra,
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
        window_label=window_label or RANKING_WINDOW_LABEL,
        balance=balance,
        commission=commission,
        slippage=slippage,
        daily_filter=daily_filter,
        elapsed_s=time.monotonic() - t0,
    )
