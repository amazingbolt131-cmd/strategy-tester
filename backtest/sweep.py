"""Full multi-venue, multi-strategy, multi-symbol sweep and GPT-ready report builder."""

from __future__ import annotations

import gc
import time
from typing import Callable

from .config import (
    DEFAULT_K,
    DEFAULT_PERIOD,
    PERIODS,
    STRATEGIES,
    STRATEGY_BY_ID,
    SYMBOLS,
    VENUE_LABELS,
    VENUE_ORDER,
    resolve_strategy,
    symbols_for,
    venue_enabled,
)
from .data import period_window
from .runner import run_backtest
from .v31 import UNIT, WEIGHTS

# Code-verified detailed specifications extracted from backtest/config.py and js/v31/*.js
STRATEGY_SPECS: dict[str, dict] = {
    "micro_1h": {
        "signal_tf": "1h (closed bars)",
        "execution_tf": "Entry at next 1h open; SL/TP checked on 5m bars",
        "htf_filter_tf": "1d BTC RSI(14) + optional 1d HAMA colour filter (Pivex)",
        "indicators": (
            "EMA(21) and EMA(55) on 1h close; Supertrend(factor=2.5, ATR=10) on 1h HLC; "
            "RSI(14) on 1h close; EMA(20) +/- ATR(10) band on 1h (Wilder RMA ATR); "
            "BTCUSDT daily RSI(14); optional Daily HAMA (CO=EMA(25) of prior (O+C)/2, CC=EMA(20) of OHLC4)."
        ),
        "entry_rules": (
            "Seq5 mean-reversion pullback inside trend. Quality score (0-4): for Long, "
            "(EMA21 > EMA55) + (Supertrend uptrend) + (RSI14 > 50) + (close > EMA20) >= 3; "
            "opposite for Short. Trigger: for Long, prev 1h low <= prev lower band AND prev close <= prev lower band, "
            "current 1h close > lower band AND bullish bar (close > open); opposite for Short. "
            "Fills at next 1h open. Strictly one Micro trade open at a time per symbol."
        ),
        "exit_rules": (
            "Monitored on 5m bars from entry onward. If both SL and TP occur inside the same 5m bar, "
            "SL is filled first. Open trades at end of test close at the last 5m bar close (END)."
        ),
        "tp_sl": (
            "SL = entry -/+ 1.5 x ATR(10) of signal 1h bar; "
            "TP = entry +/- 3.0 x ATR(10) (fixed 2.0R target)."
        ),
        "filters": (
            "Macro BTC daily RSI(14) gate: skip Long if last closed BTC daily RSI < 30; "
            "skip Short if > 70. Plus Daily HAMA colour lock on Pivex when enabled."
        ),
        "sizing_and_risk": (
            f"Base risk $125.00 including a 6 bps (0.0006) fee buffer: notional = 125 / (1.5*ATR10/entry + 0.0006). "
            f"Pivex combo scaling: x weight ({WEIGHTS['Micro']:.6f}) x internal k ({DEFAULT_K:.0f}) = "
            f"${UNIT['Micro'] * WEIGHTS['Micro'] * DEFAULT_K:,.2f} risk per trade (flat, no loss-step)."
        ),
        "venue_differences": "Enabled on Pivex only (binance=False). Single-entry only; no adds.",
        "fees_slippage": "Engine computes 0.04%/side taker fee; user commission/slippage settings adjust on top.",
    },
    "turtle_5m": {
        "signal_tf": "5m trigger + 30m Turtle bias (6x 5m buckets)",
        "execution_tf": "5m open fill; intra-bar OHLC path walk for SL/TP",
        "htf_filter_tf": "30m NY-session Turtle + optional 1d HAMA filter (Pivex)",
        "indicators": (
            "30m Turtle Bias: NY session roll at 17:00 America/New_York, Donchian 20-session entry high/low, "
            "10-session exit low/high, session Wilder ATR(20), 2.0 x ATR(20) initial stop. "
            "5m Trigger: SMA(200) on 5m close, 20-bar rolling high/low, Wilder ATR(14), "
            "and rolling mean of last 1,000 trigger ATR(14) values (fallback to current ATR14 if <1,000 triggers)."
        ),
        "entry_rules": (
            "Long: 5m close < SMA(200) AND close <= 20-bar low x 1.06 (within 6% of 20-bar low) "
            "AND previous completed 30m Turtle bias == +1. "
            "Short: 5m close > SMA(200) AND close >= 20-bar high x 0.94 (within 6% of 20-bar high) "
            "AND previous completed 30m Turtle bias == -1. Fills at next 5m open."
        ),
        "exit_rules": (
            "Evaluated on 5m OHLC via TradingView bar-path order (O->H->L->C if H-O <= O-L, else O->L->H->C). "
            "Open positions at end of window are excluded if unclosed (wait for TP or SL)."
        ),
        "tp_sl": (
            "SL distance = avg_trigger_ATR(14, lookback=1000) x (70/3) (~23.333 x ATR14) from current average entry. "
            "TP1 = +6.0% from average entry (closes full position on Pivex, or tranche 1 on Binance); "
            "TP2 = +3.0% from average entry (closes tranche 2 add on Binance)."
        ),
        "filters": "30m NY-session Turtle bias required; Pivex also enforces 1d HAMA colour match when Daily HAMA filter is on.",
        "sizing_and_risk": (
            f"Pivex: $10,000 x weight ({WEIGHTS['Turtle']:.6f}) x internal k ({DEFAULT_K:.0f}) = "
            f"${UNIT['Turtle'] * WEIGHTS['Turtle'] * DEFAULT_K:,.2f} notional in a single fill. "
            "Binance: $10,000 fixed full book ($5,000 first fill + $5,000 add), not scaled by k."
        ),
        "venue_differences": (
            "Pivex is single-entry (100% size, no add, one 6% TP). "
            "Binance is multi-entry: opens 50% ($5,000), adds the other 50% ($5,000) at next 5m open if a 5m close is >= +3.0% "
            "in favour while 30m bias still agrees; TP1 (6%) closes tranche 1 only, TP2 (3% from avg) closes tranche 2."
        ),
        "fees_slippage": "Engine applies 0.04%/side; user commission/slippage settings adjust on top.",
    },
    "ml_5m": {
        "signal_tf": "5m closed bars",
        "execution_tf": "Next 5m open (+/- $0.01 tick); SL/TP via 5m OHLC path walk",
        "htf_filter_tf": "Optional 1d HAMA colour filter (Pivex only)",
        "indicators": (
            "Lorentzian distance kNN (k=12 neighbours, maxBarsBack=2000, every 4th bar sampled, label = 4-bar future close direction) "
            "over 5 normalized [0,1] features: RSI(14), WaveTrend(10, 11) on hlc3, CCI(20), ADX(20), RSI(9). "
            "Volatility filter: RMA(TR, 1) > RMA(TR, 10). Regime filter: adaptive slope vs EMA(200) >= -0.1 on ohlc4. "
            "Rational Quadratic kernel: lookback=8, relativeWeight=8.0, startAtBar=25."
        ),
        "entry_rules": (
            "Short only (longs disabled). "
            "Short: kNN prediction < 0 AND kernel falling (yh[t] < yh[t-1]) AND volatility filter AND regime filter. "
            "Fills at next 5m open - $0.01 tick. One position at a time."
        ),
        "exit_rules": (
            "Intra-bar SL or TP evaluated via TV OHLC bar-path walk. "
            "Time stop: if bars since last same-direction signal >= 300 bars (25 hours), exits at next 5m open (- side x $0.01 tick, MaxBars)."
        ),
        "tp_sl": (
            "SL = 3.0% adverse from entry (filled with $0.01 adverse tick slippage). "
            "TP = 3.1% favourable from entry. Time stop at 300 5m bars."
        ),
        "filters": "Volatility + Regime (-0.1) + Rational Quadratic Kernel slope; Pivex also applies 1d HAMA colour gate when enabled.",
        "sizing_and_risk": (
            f"Pivex: $10,000 x weight ({WEIGHTS['ML']:.6f}) x internal k ({DEFAULT_K:.0f}) = "
            f"${UNIT['ML'] * WEIGHTS['ML'] * DEFAULT_K:,.2f} notional per trade. "
            "Binance: $10,000 fixed notional per trade (not scaled by k)."
        ),
        "venue_differences": "Single-entry on both Pivex and Binance; differs only in notional scaling and Pivex Daily HAMA filter.",
        "fees_slippage": "Engine applies 0.04%/side + $0.01 tick on entry, SL, and MaxBars exit; user commission/slippage adjust on top.",
    },
    "hama_30m": {
        "signal_tf": "30m closed bars",
        "execution_tf": "30m signal bar close; stops checked on 30m H/L",
        "htf_filter_tf": "1h HAMA lock + 1h EMA(200) + optional 1d HAMA filter",
        "indicators": (
            "HAMA Stable on 30m: CO = EMA(25) of prior (O+C)/2, CC = EMA(20) of OHLC4, Wilder ATR(14), "
            "gap >= 0.3 x ATR(14). 1h HAMA lock (CO=25, CC=20) from strictly prior closed 1h bar. "
            "1h EMA(200) trend filter. 10-bar swing high/low on 30m."
        ),
        "entry_rules": (
            "Enter at 30m close when HAMA Stable flips colour (CC vs CO with |CC - CO| >= 0.3 x ATR14), "
            "strictly prior closed 1h HAMA colour agrees, latest closed 1h close is on the matching side of 1h EMA(200), "
            "and Daily HAMA colour agrees (when enabled). One position at a time."
        ),
        "exit_rules": (
            "Stop hit on 30m bar H/L (filled at stop price), opposite 30m HAMA Stable flip (FLIP, filled at 30m close), "
            "or forced close at end of test window (END). No fixed TP target."
        ),
        "tp_sl": (
            "SL = 10-bar swing low (Long) / 10-bar swing high (Short), widened if distance < max(0.20 x ATR(14), min_stop), "
            "where min_stop = $0.25 on SOLUSDT or entry x 0.25 / SOL_1h_close on other symbols. No fixed TP (rides trend to flip)."
        ),
        "filters": "1h HAMA colour lock + 1h close vs EMA(200) + optional 1d HAMA colour filter.",
        "sizing_and_risk": (
            f"Flat risk per trade (max_step=1, no loss-step). Base unit ${UNIT['HAMA30']:.2f}; "
            f"Pivex scaled = unit x weight ({WEIGHTS['HAMA30']:.6f}) x internal k ({DEFAULT_K:.0f}) = "
            f"${UNIT['HAMA30'] * WEIGHTS['HAMA30'] * DEFAULT_K:,.2f} risk/trade (qty = risk / |entry - stop|)."
        ),
        "venue_differences": "Enabled on Pivex only (binance=False). Single-entry.",
        "fees_slippage": "Engine applies 0.04%/side; user commission/slippage settings adjust on top.",
    },
    "hama_1h": {
        "signal_tf": "1h closed bars",
        "execution_tf": "1h signal bar close; stops checked on 1h H/L",
        "htf_filter_tf": "4h HAMA lock + 1h EMA(200) + optional 1d HAMA filter",
        "indicators": (
            "HAMA Stable on 1h: CO = EMA(25) of prior (O+C)/2, CC = EMA(20) of OHLC4, Wilder ATR(14), "
            "gap >= 0.3 x ATR(14). 4h HAMA lock from strictly prior closed 4h bar. "
            "1h EMA(200) trend filter. 10-bar swing high/low on 1h."
        ),
        "entry_rules": (
            "Enter at 1h close on HAMA Stable colour flip when |CC - CO| >= 0.3 x ATR(14), "
            "prior closed 4h HAMA colour agrees, 1h close vs EMA(200) agrees, and Daily HAMA agrees (when enabled)."
        ),
        "exit_rules": (
            "Stop hit on 1h bar H/L (STOP), opposite 1h HAMA Stable flip (FLIP at 1h close), "
            "or forced close at end of test (END). No fixed TP."
        ),
        "tp_sl": (
            "SL = 10-bar swing low/high, widened to at least max(0.20 x ATR(14), SOL-scaled $0.25). "
            "No fixed TP (exits on stop or opposite HAMA flip)."
        ),
        "filters": "4h HAMA colour lock + 1h close vs EMA(200) + optional 1d HAMA colour filter.",
        "sizing_and_risk": (
            f"Loss-step sizing (1 to 8 steps, per symbol). Base unit ${UNIT['HAMA1h']:.2f}; "
            f"Pivex step 1 = unit x weight ({WEIGHTS['HAMA1h']:.6f}) x internal k ({DEFAULT_K:.0f}) = "
            f"${UNIT['HAMA1h'] * WEIGHTS['HAMA1h'] * DEFAULT_K:,.2f} risk; each losing trade adds +1 step up to 8x "
            f"(${UNIT['HAMA1h'] * WEIGHTS['HAMA1h'] * DEFAULT_K * 8:,.2f}); a win resets to step 1."
        ),
        "venue_differences": "Enabled on Pivex only (binance=False). Single-entry.",
        "fees_slippage": "Engine applies 0.04%/side; user commission/slippage settings adjust on top.",
    },
    "s5_15m": {
        "signal_tf": "15m closed bars + 30m Turtle bias",
        "execution_tf": "1m open fills; SL/TP checked every 1m bar",
        "htf_filter_tf": "30m NY-session Turtle bias + optional 1d HAMA filter (Pivex)",
        "indicators": (
            "30m Turtle Bias: NY 17:00 session roll, Donchian 20/10, session Wilder ATR(20), 2.0 x ATR(20) stop. "
            "15m Extreme Reversal: SMA(200) on 15m close, 20-bar high/low, 6% proximity, Wilder ATR(14), "
            "and rolling mean of last 1,000 trigger ATR(14) values."
        ),
        "entry_rules": (
            "Long: 15m close < SMA(200) AND close <= 20-bar low x 1.06 AND previous closed 30m Turtle bias == +1. "
            "Short: 15m close > SMA(200) AND close >= 20-bar high x 0.94 AND previous closed 30m Turtle bias == -1. "
            "Fills at next 1m open."
        ),
        "exit_rules": (
            "Checked on every 1m bar: gap at 1m open checked first (SL before TP), then intra-minute H/L (SL before TP). "
            "Open positions at end of test are marked at the final 1m close (END)."
        ),
        "tp_sl": (
            "SL distance AD = avg_trigger_ATR(14, lookback=1000) x (70/3) (~23.333 x ATR14), updated each 15m bar from avg entry. "
            "TP1 = +6.0% from average entry; TP2 = +3.0% from average entry (for Binance add tranche)."
        ),
        "filters": "Previous closed 30m Turtle bias required; Pivex enforces 1d HAMA colour filter when enabled.",
        "sizing_and_risk": (
            f"Pivex: step 1 risk = ${UNIT['S5']:.2f} x weight ({WEIGHTS['S5']:.6f}) x internal k ({DEFAULT_K:.0f}) = "
            f"${UNIT['S5'] * WEIGHTS['S5'] * DEFAULT_K:,.2f}; 2x after 2 consecutive losing baskets (cap 2x = "
            f"${UNIT['S5'] * WEIGHTS['S5'] * DEFAULT_K * 2:,.2f}); win resets to 1x. "
            "Binance: base risk $22.09 (not scaled by k), doubles after every losing basket up to 5x ($110.43); win resets to 1x."
        ),
        "venue_differences": (
            "Pivex is single-entry (no add; TP1 at +6% closes entire trade). "
            "Binance is multi-entry: adds an equal-notional tranche 2 at next 1m open when a 15m close is >= +3.0% in favour "
            "while 30m bias agrees; TP1 (+6% from avg) closes tranche 1 only, TP2 (+3% from avg) closes tranche 2."
        ),
        "fees_slippage": "Engine applies 0.04%/side; user commission/slippage settings adjust on top.",
    },
    "sol1m_morning": {
        "signal_tf": "1m closed bars + 3m HAMA lock",
        "execution_tf": "1m close + $0.06 spread",
        "htf_filter_tf": "3m completed-bar HAMA lock + 08:00 Europe/Kyiv overnight move filter",
        "indicators": (
            "1m HAMA Stable: CO = EMA(25) of prior (O+C)/2, CC = EMA(20) of OHLC4, Wilder ATR(10), gap >= 0.3 x ATR(10). "
            "3m HAMA lock from previous completed 3m bar. Kyiv 00:00->07:59 close-to-close overnight return."
        ),
        "entry_rules": (
            "At 08:00 Europe/Kyiv: move = close(07:59) / close(prev 23:59) - 1. "
            "+1% to +3% enables Long-only day; -1% to -3% enables Short-only day; otherwise day is skipped. "
            "Between 08:00 and 23:59 Kyiv, enter on 1m HAMA flip matching 3m lock and day direction at 1m close + dir x $0.06 spread."
        ),
        "exit_rules": (
            "Fixed -$100 stop checked intra-minute on 1m H/L, opposite 1m HAMA flip at 1m close (flip), "
            "or 23:59 Kyiv end-of-day close (day_end). First losing trade ends the day (maxloss=1); "
            "intraday equity <= -$600 busts and stops the day (BUST). No fixed TP."
        ),
        "tp_sl": "SL = -$100 per trade (approx $0.25% move on $40,000 notional). No TP target.",
        "filters": "08:00 Kyiv +/-1% to +/-3% overnight move band + 3m HAMA lock. Daily HAMA filter and k do not apply.",
        "sizing_and_risk": "Fixed $40,000 notional per trade (qty = round(40000/close, 2)), not scaled by k.",
        "venue_differences": "Enabled on Pivex only (binance=False). Single position at a time.",
        "fees_slippage": "Built-in $0.06/unit entry spread (adjustable=False, no extra 0.04% fee); user slippage adjusts if non-zero.",
    },
    "dg_grid": {
        "signal_tf": "1h closed bars + 4h HAMA lock",
        "execution_tf": "1m open entries/exits; 1m H/L grid adds, unloads & stop",
        "htf_filter_tf": "4h HAMA colour lock",
        "indicators": (
            "Unanimous 3-filter agreement on closed 1h bars: "
            "(F1) 1h close vs EMA(200); "
            "(F2) 1h Turtle 20/10 (NY 17:00 session roll, Wilder ATR(20), 2x ATR stop); "
            "(F3) 1h HAMA Stable (CO=25, CC=20, ATR=14, gap=0.3xATR) locked to strictly prior closed 4h HAMA colour."
        ),
        "entry_rules": (
            "When combined filter (F1==F2==F3) flips to +1 or -1 on a 1h close, opens Level 1 ($1,000 notional) at next 1m open."
        ),
        "exit_rules": (
            "1) Unload: 1.0% pullback (2.0 x 0.5% step) from basket extreme closes ONLY the newest level; "
            "rearms after a new extreme. If all levels unload, basket closes (UNLOADED). "
            "2) Opposite unanimous filter flip closes whole basket at next 1m open (COLOUR). "
            "3) Hard Stop closes whole basket (STOP). Open basket at test end marked END."
        ),
        "tp_sl": (
            "Adds up to 8 levels ($1,000 notional each, max $8,000) every +0.5% in favour from last add. "
            "Hard SL = 10.0% adverse from basket average entry. No fixed TP (rides trend with trailing unloads)."
        ),
        "filters": "All 3 filters (1h EMA200, 1h Turtle 20/10, 1h/4h HAMA Stable) must agree.",
        "sizing_and_risk": "Flat $1,000 notional per grid level (up to $8,000 total). No loss-step.",
        "venue_differences": "Binance only (pivex=False). Uses multi-level grid.",
        "fees_slippage": "Engine applies 0.04%/side on each level fill/unload/close; user commission/slippage adjust on top.",
    },
    "er_grid": {
        "signal_tf": "15m closed bars",
        "execution_tf": "1m fills with tick-floored grid orders",
        "htf_filter_tf": "None",
        "indicators": (
            "15m Extreme Reversal: SMA(20) basis (lenBB=20, multBB=0), 1-bar high/low (turtleLen=1), "
            "50% proximity, Wilder ATR(14), rolling mean of last 1,000 trigger ATR(14) values."
        ),
        "entry_rules": (
            "Enters OPPOSITE the 15m Extreme Reversal signal (dir=D_OPP, wait=0). "
            "Arms virtual anchor at signal open; grid levels fill on 1m bars as price moves against anchor."
        ),
        "exit_rules": (
            "Basket closes on hard SL (STOP), 6.0% TP (TP), or end of test window (END). "
            "No breakeven exit (exitPct=-1.0)."
        ),
        "tp_sl": (
            "Grid adds up to 5 units (step=0.20%, pullback rev=0.10%) using thirds-carry sizing (1/3 R, 2/3 R, 1 R cycle). "
            "SL = 1.0 x AD from anchor where AD = avg_trigger_ATR(14, 1000) x (9.0 / 3 = 3.0 x ATR14). "
            "TP = +6.0% from anchor."
        ),
        "filters": "No HTF or Daily HAMA filter.",
        "sizing_and_risk": (
            "Loss-step sizing (steps 1 to 10, per coin): $0.360237 risk per unit at step 1, "
            "+1 step per losing basket up to 10 ($3.60); win resets to step 1."
        ),
        "venue_differences": "Binance only (pivex=False). Uses multi-level thirds-carry grid.",
        "fees_slippage": "Engine applies 0.04%/side; user commission/slippage adjust on top.",
    },
    "hama_grid_5m": {
        "signal_tf": "5m closed bars",
        "execution_tf": "1m fills against flip anchor",
        "htf_filter_tf": "1h HAMA colour-count share over 3 days (72 bars)",
        "indicators": (
            "5m HAMA Stable (CO=25, CC=20, ATR=14, gap=0.3xATR). "
            "1h HAMA colour-count filter over rolling 3-day window (72x 1h bars), 65% threshold."
        ),
        "entry_rules": (
            "5m HAMA flip arms a counter-trend grid anchored at flip close p, provided the 1h 3-day colour count "
            "does not have >= 65% share in the opposite colour (neutral <65% allows both sides). "
            "Levels fill on 1m bars only when price pulls back against the flip."
        ),
        "exit_rules": "Entire basket closes on hard SL (STOP), TP (TP), or end of window (END).",
        "tp_sl": (
            "Adds up to 5 levels ($1,000 notional each, max $5,000) every 0.75% against flip anchor p. "
            "TP = +6.0% from anchor p. SL = (5 + 1) x 0.75% = 4.5% adverse from anchor p."
        ),
        "filters": "1h 3-day 65% HAMA colour-count directional bias filter.",
        "sizing_and_risk": "Flat $1,000 notional per grid level (up to $5,000). No loss-step.",
        "venue_differences": "Binance only (pivex=False). Uses multi-level grid.",
        "fees_slippage": "Engine applies 0.04%/side; user commission/slippage adjust on top.",
    },
    "hama_grid_1m": {
        "signal_tf": "1m closed bars + 15m HAMA lock",
        "execution_tf": "1m fills against flip anchor",
        "htf_filter_tf": "15m HAMA lock + 4h HAMA colour-count share over 5 days (30 bars)",
        "indicators": (
            "1m HAMA Stable (CO=25, CC=20, ATR=14, gap=0.3xATR) locked to strictly prior closed 15m HAMA colour. "
            "4h HAMA colour-count filter over rolling 5-day window (30x 4h bars), 65% threshold."
        ),
        "entry_rules": (
            "1m HAMA flip (agreeing with 15m HAMA lock) arms a grid anchored at flip close p, provided 4h 5-day colour count "
            "does not have >= 65% share in the opposite colour (neutral allows both)."
        ),
        "exit_rules": "Entire basket closes on hard SL (STOP), TP (TP), or end of window (END).",
        "tp_sl": (
            "Adds up to 5 levels ($1,000 notional each, max $5,000) every 0.30% against flip anchor p. "
            "TP = +6.0% from anchor p. SL = (5 + 1) x 0.30% = 1.8% adverse from anchor p."
        ),
        "filters": "15m HAMA colour lock + 4h 5-day 65% HAMA colour-count filter.",
        "sizing_and_risk": "Flat $1,000 notional per grid level (up to $5,000). No loss-step.",
        "venue_differences": "Binance only (pivex=False). Uses multi-level grid.",
        "fees_slippage": "Engine applies 0.04%/side; user commission/slippage settings adjust on top.",
    },
}


def combo_plan() -> tuple[list[dict], list[dict]]:
    """Return (canonical_combos, execution_order_combos_grouped_by_symbol)."""
    canonical: list[dict] = []
    idx = 0
    for venue in VENUE_ORDER:
        for strategy in STRATEGIES:
            if not venue_enabled(strategy, venue):
                continue
            for symbol in symbols_for(strategy["id"], venue):
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

    # Group execution by symbol so each symbol's 70-day 1m tape is loaded once
    # inside the bounded LRU cache. Run BTCUSDT and SOLUSDT first since
    # micro_1h uses BTC daily and HAMA uses SOL 1h.
    priority = {"BTCUSDT": 0, "SOLUSDT": 1}
    exec_order = sorted(
        canonical,
        key=lambda item: (priority.get(item["symbol"], 2), item["symbol"], item["order_idx"]),
    )
    return canonical, exec_order


def _fmt_pf(pf: float | None) -> str:
    if pf is None:
        return "∞"
    return f"{pf:.2f}"


def _fmt_ratio(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}"


def _fmt_money(v: float, signed: bool = False) -> str:
    if v < 0:
        return f"-${abs(v):,.2f}"
    text = f"${v:,.2f}"
    return f"+{text}" if signed and v > 0 else text


def _fmt_pct(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "—"
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{v:.2f}%"


def _build_analysis(
    rows: list[dict],
    venue_aggs: list[dict],
    strat_aggs: list[dict],
    balance: float,
) -> list[str]:
    notes: list[str] = []
    ok_rows = [r for r in rows if not r.get("error")]
    active = [r for r in ok_rows if r["stats"]["total_trades"] > 0]
    profitable = [r for r in active if r["stats"]["net_profit"] > 0]
    losing = [r for r in active if r["stats"]["net_profit"] < 0]

    total_net = sum(r["stats"]["net_profit"] for r in ok_rows)
    total_trades = sum(r["stats"]["total_trades"] for r in ok_rows)
    total_comm = sum(r["stats"]["commission_paid"] for r in ok_rows)

    notes.append(
        f"Across {len(ok_rows)} completed setups ({len(active)} with >=1 trade, {len(ok_rows) - len(active)} with 0 trades), "
        f"{len(profitable)} were net profitable and {len(losing)} lost money; combined unweighted sum of net P&L across all setups is "
        f"{_fmt_money(total_net, True)} over {total_trades:,} total fills (commissions paid: {_fmt_money(total_comm)})."
    )

    if len(venue_aggs) == 2:
        pv, bn = venue_aggs[0], venue_aggs[1]
        notes.append(
            f"Venue comparison: {pv['venue_label']} ({pv['profitable']}/{pv['combos']} profitable, "
            f"sum {_fmt_money(pv['net_profit'], True)}, PF {_fmt_pf(pv['profit_factor'])}, worst DD {_fmt_pct(pv['worst_dd_pct'], False)}) "
            f"vs {bn['venue_label']} ({bn['profitable']}/{bn['combos']} profitable, "
            f"sum {_fmt_money(bn['net_profit'], True)}, PF {_fmt_pf(bn['profit_factor'])}, worst DD {_fmt_pct(bn['worst_dd_pct'], False)})."
        )

    consistent = [
        s for s in strat_aggs if s["active_coins"] >= 2 and s["profitable_coins"] / s["coins_tested"] >= 0.6
    ]
    consistent.sort(key=lambda s: (s["profitable_coins"] / s["coins_tested"], s["total_net_profit"]), reverse=True)
    if consistent:
        top_c = consistent[:3]
        desc = "; ".join(
            f"{s['venue_label']} {s['strategy_label']} ({s['profitable_coins']}/{s['coins_tested']} coins profitable, "
            f"sum {_fmt_money(s['total_net_profit'], True)}, PF {_fmt_pf(s['profit_factor'])})"
            for s in top_c
        )
        notes.append(f"Most consistent strategies across their tested coins (>=60% coins profitable): {desc}.")
    else:
        notes.append("No strategy with >=2 active coins achieved >=60% profitable coins in this window.")

    ranked_active = sorted(active, key=lambda r: r["stats"]["net_profit"], reverse=True)
    if ranked_active:
        best = ranked_active[0]
        worst = ranked_active[-1]
        notes.append(
            f"Best single setup: {best['venue_label']} · {best['strategy_label']} · {best['symbol_label']} "
            f"({_fmt_money(best['stats']['net_profit'], True)}, {_fmt_pct(best['stats']['net_profit_pct'])}, "
            f"PF {_fmt_pf(best['stats']['profit_factor'])}, {best['stats']['total_trades']} trades, "
            f"Max DD {_fmt_money(-best['stats']['max_drawdown'])} / {best['stats']['max_drawdown_pct']:.2f}%). "
            f"Worst single setup: {worst['venue_label']} · {worst['strategy_label']} · {worst['symbol_label']} "
            f"({_fmt_money(worst['stats']['net_profit'], True)}, {_fmt_pct(worst['stats']['net_profit_pct'])}, "
            f"PF {_fmt_pf(worst['stats']['profit_factor'])}, {worst['stats']['total_trades']} trades, "
            f"Max DD {_fmt_money(-worst['stats']['max_drawdown'])} / {worst['stats']['max_drawdown_pct']:.2f}%)."
        )

    # Profit concentration among positive setups
    pos_only = [r for r in ranked_active if r["stats"]["net_profit"] > 0]
    gross_pos_combos = sum(r["stats"]["net_profit"] for r in pos_only)
    if gross_pos_combos > 0 and len(pos_only) >= 3:
        top1_share = pos_only[0]["stats"]["net_profit"] / gross_pos_combos * 100.0
        top3_share = sum(r["stats"]["net_profit"] for r in pos_only[:3]) / gross_pos_combos * 100.0
        notes.append(
            f"Profit concentration: the #1 winning setup accounts for {top1_share:.1f}% of all positive-setup gains, "
            f"and the top 3 setups account for {top3_share:.1f}% (across {len(pos_only)} winning setups)."
        )

    # Drawdown risk check
    high_dd = sorted(
        [r for r in active if r["stats"]["max_drawdown_pct"] >= 10.0],
        key=lambda r: r["stats"]["max_drawdown_pct"],
        reverse=True,
    )
    if high_dd:
        top_dd = ", ".join(
            f"{r['venue_label']} {r['strategy_label']} {r['symbol_label']} ({r['stats']['max_drawdown_pct']:.1f}% / {_fmt_money(-r['stats']['max_drawdown'])})"
            for r in high_dd[:4]
        )
        notes.append(f"Drawdown risk (>=10% of ${_money_plain(balance)} starting balance): {len(high_dd)} setups breached 10% DD; largest: {top_dd}.")
    else:
        max_dd_row = max(active, key=lambda r: r["stats"]["max_drawdown_pct"]) if active else None
        if max_dd_row:
            notes.append(
                f"Drawdown risk: no single setup breached 10% of starting balance; highest DD was "
                f"{max_dd_row['venue_label']} {max_dd_row['strategy_label']} {max_dd_row['symbol_label']} at "
                f"{max_dd_row['stats']['max_drawdown_pct']:.2f}% ({_fmt_money(-max_dd_row['stats']['max_drawdown'])})."
            )

    # Low-sample & zero-trade caution
    low_sample = [r for r in active if r["stats"]["total_trades"] < 5]
    zero_trades = [r for r in ok_rows if r["stats"]["total_trades"] == 0]
    notes.append(
        f"Sample-size caution: {len(zero_trades)} setups produced 0 trades and {len(low_sample)} active setups had fewer than 5 trades; "
        f"high PF or win-rate figures on <5 trades should be treated as low statistical confidence."
    )

    # Long vs Short skew
    long_trades = sum(r["stats"]["long_trades"] for r in ok_rows)
    short_trades = sum(r["stats"]["short_trades"] for r in ok_rows)
    long_pnl = sum(r["stats"]["long_net_profit"] for r in ok_rows)
    short_pnl = sum(r["stats"]["short_net_profit"] for r in ok_rows)
    notes.append(
        f"Directional split across all fills: Longs = {long_trades:,} trades ({_fmt_money(long_pnl, True)}), "
        f"Shorts = {short_trades:,} trades ({_fmt_money(short_pnl, True)})."
    )
    return notes


def _money_plain(v: float) -> str:
    return f"{v:,.2f}"


def _build_markdown(report: dict) -> str:
    s = report["summary"]
    cfg = report["settings"]
    lines: list[str] = []
    lines.append("# Futures Strategy Tester — Full Sweep Analysis Report")
    lines.append("")
    lines.append("## 1. Run Context & Test Settings")
    lines.append(f"- **Test window:** {report['window_label']}")
    lines.append(f"- **Starting balance (denominator for % return & DD%):** ${cfg['balance']:,.2f}")
    lines.append(f"- **Commission:** {cfg['commission']:.4f}% per side (applied on entry and exit notional; SOL 1m Morning uses fixed $0.06 spread)")
    lines.append(f"- **Slippage:** {cfg['slippage']:.4f}% per fill")
    lines.append(f"- **Daily HAMA filter (Pivex):** {'ON' if cfg['daily_filter'] else 'OFF'}")
    lines.append(f"- **Internal Pivex combo multiplier (k):** {cfg['internal_k']:.0f} (v3.1 live default; Binance strategies are not scaled by k)")
    lines.append(f"- **Setups evaluated:** {s['completed_combos']} / {s['total_combos']} ({s['failed_combos']} failed) in {s['elapsed_s']:.1f}s")
    lines.append("")

    lines.append("## 2. Executive Summary & Automated Analysis")
    lines.append("> **Note:** The bullet observations below are **Automated Analysis** generated deterministically from the backtest numbers in this report.")
    lines.append("")
    lines.append(f"- **Active setups (>=1 trade):** {s['active_combos']} / {s['completed_combos']}")
    lines.append(f"- **Profitable / Losing / Flat setups:** {s['profitable_combos']} / {s['losing_combos']} / {s['flat_combos']}")
    lines.append(f"- **Combined unweighted Net P&L across all setups:** {_fmt_money(s['total_net_profit'], True)}")
    lines.append(f"- **Total closed trades across all setups:** {s['total_trades']:,}")
    lines.append(f"- **Aggregate Win Rate:** {s['overall_win_rate']:.2f}% | **Aggregate Profit Factor:** {_fmt_pf(s['overall_profit_factor'])}")
    lines.append(f"- **Total Commissions Paid:** {_fmt_money(s['total_commission'])}")
    lines.append("")
    for bullet in report["analysis"]:
        lines.append(f"- {bullet}")
    lines.append("")

    lines.append("## 3. Venue Aggregates")
    lines.append("")
    lines.append("| Venue | Combos | Active | Profitable | Trades (L/S) | Win Rate | Profit Factor | Net P&L $ | Avg Return % | Worst Combo Max DD | Commissions |")
    lines.append("|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|")
    for v in report["venue_aggregates"]:
        lines.append(
            f"| {v['venue_label']} | {v['combos']} | {v['active']} | {v['profitable']} | "
            f"{v['total_trades']} ({v['long_trades']}L/{v['short_trades']}S) | {v['win_rate']:.2f}% | "
            f"{_fmt_pf(v['profit_factor'])} | {_fmt_money(v['net_profit'], True)} | {_fmt_pct(v['avg_net_pct'])} | "
            f"{_fmt_money(-v['worst_dd'])} ({v['worst_dd_pct']:.2f}%) | {_fmt_money(v['commission_paid'])} |"
        )
    lines.append("")

    lines.append("## 4. Per-Strategy Aggregates (by Venue)")
    lines.append("")
    lines.append("| Venue | Strategy | Coins | Active | Profitable | Trades (L/S) | Win Rate | PF | Total Net P&L | Avg Net % | Expectancy $/trade | Worst Combo DD | Avg Time in Mkt |")
    lines.append("|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for st in report["strategy_aggregates"]:
        tim = f"{st['avg_time_in_market_pct']:.1f}%" if st["avg_time_in_market_pct"] is not None else "—"
        lines.append(
            f"| {st['venue_label']} | {st['strategy_label']} | {st['coins_tested']} | {st['active_coins']} | "
            f"{st['profitable_coins']} | {st['total_trades']} ({st['long_trades']}L/{st['short_trades']}S) | "
            f"{st['win_rate']:.2f}% | {_fmt_pf(st['profit_factor'])} | {_fmt_money(st['total_net_profit'], True)} | "
            f"{_fmt_pct(st['avg_net_pct'])} | {_fmt_money(st['expectancy'], True)} | "
            f"{_fmt_money(-st['worst_dd'])} ({st['worst_dd_pct']:.2f}%) | {tim} |"
        )
    lines.append("")

    lines.append("## 5. Best & Worst Setups")
    lines.append("")
    lines.append("### Top 5 Setups (by Net P&L)")
    for i, r in enumerate(report["best_5"], 1):
        st = r["stats"]
        lines.append(
            f"{i}. **{r['venue_label']} · {r['strategy_label']} · {r['symbol_label']}**: "
            f"{_fmt_money(st['net_profit'], True)} ({_fmt_pct(st['net_profit_pct'])}), "
            f"Trades {st['total_trades']} ({st['long_trades']}L/{st['short_trades']}S), "
            f"WR {st['win_rate']:.2f}%, PF {_fmt_pf(st['profit_factor'])}, "
            f"Max DD {_fmt_money(-st['max_drawdown'])} ({st['max_drawdown_pct']:.2f}%), "
            f"Expectancy {_fmt_money(st['expectancy'], True)}/trade"
        )
    lines.append("")
    lines.append("### Bottom 5 Setups (by Net P&L)")
    for i, r in enumerate(report["worst_5"], 1):
        st = r["stats"]
        lines.append(
            f"{i}. **{r['venue_label']} · {r['strategy_label']} · {r['symbol_label']}**: "
            f"{_fmt_money(st['net_profit'], True)} ({_fmt_pct(st['net_profit_pct'])}), "
            f"Trades {st['total_trades']} ({st['long_trades']}L/{st['short_trades']}S), "
            f"WR {st['win_rate']:.2f}%, PF {_fmt_pf(st['profit_factor'])}, "
            f"Max DD {_fmt_money(-st['max_drawdown'])} ({st['max_drawdown_pct']:.2f}%), "
            f"Expectancy {_fmt_money(st['expectancy'], True)}/trade"
        )
    lines.append("")

    lines.append("## 6. Full Ranked Results Table (All Combos)")
    lines.append("")
    lines.append(
        "| # | Venue | Strategy | Coin | Net P&L $ | Net % | Gross Profit | Gross Loss | PF | Max DD $ | Max DD % | "
        "Ret/DD | Trades | W/L | Win Rate | Expectancy $ | Avg Win | Avg Loss | W/L Ratio | Largest Win | Largest Loss | "
        "Max Consec W/L | Sharpe | Long (N / WR% / $) | Short (N / WR% / $) | Time in Mkt | Avg Dur (h) | Fees | Exit Reasons |"
    )
    lines.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---|")
    for r in report["ranked"]:
        if r.get("error"):
            lines.append(f"| {r['rank']} | {r['venue_label']} | {r['strategy_label']} | {r['symbol_label']} | ERROR: {r['error']} | | | | | | | | | | | | | | | | | | | | | | | | |")
            continue
        st = r["stats"]
        tim = f"{st['time_in_market_pct']:.1f}%" if st["time_in_market_pct"] is not None else "—"
        reasons = ", ".join(f"{k}:{v}" for k, v in sorted(st["exit_reasons"].items())) or "—"
        lines.append(
            f"| {r['rank']} | {r['venue_label']} | {r['strategy_label']} | {r['symbol_label']} | "
            f"{_fmt_money(st['net_profit'], True)} | {_fmt_pct(st['net_profit_pct'])} | "
            f"{_fmt_money(st['gross_profit'])} | {_fmt_money(st['gross_loss'])} | {_fmt_pf(st['profit_factor'])} | "
            f"{_fmt_money(-st['max_drawdown'])} | {st['max_drawdown_pct']:.2f}% | {_fmt_ratio(st['return_dd_ratio'])} | "
            f"{st['total_trades']} | {st['wins']}/{st['losses']} | {st['win_rate']:.2f}% | "
            f"{_fmt_money(st['expectancy'], True)} | {_fmt_money(st['avg_win'])} | {_fmt_money(st['avg_loss'])} | "
            f"{_fmt_ratio(st['win_loss_ratio'])} | {_fmt_money(st['largest_win'])} | {_fmt_money(st['largest_loss'])} | "
            f"{st['max_consec_wins']}/{st['max_consec_losses']} | {_fmt_ratio(st['sharpe'])} | "
            f"{st['long_trades']} / {st['long_win_rate']:.1f}% / {_fmt_money(st['long_net_profit'], True)} | "
            f"{st['short_trades']} / {st['short_win_rate']:.1f}% / {_fmt_money(st['short_net_profit'], True)} | "
            f"{tim} | {st['avg_duration_hours']:.1f}h | {_fmt_money(st['commission_paid'])} | {reasons} |"
        )
    lines.append("")

    lines.append("## 7. Detailed Strategy Rules & Specifications (v3.1 Engine Reference)")
    lines.append("")
    for spec in report["strategy_specs"]:
        lines.append(f"### {spec['label']} (`{spec['id']}`)")
        lines.append(f"- **Enabled Venues & Coins:** {spec['coins_summary']}")
        lines.append(f"- **Signal Timeframe:** {spec['signal_tf']}")
        lines.append(f"- **Execution & Exit Checking:** {spec['execution_tf']}")
        lines.append(f"- **Higher-Timeframe / Macro Filter TF:** {spec['htf_filter_tf']}")
        lines.append(f"- **Indicators & Exact Parameters:** {spec['indicators']}")
        lines.append(f"- **Entry Conditions:** {spec['entry_rules']}")
        lines.append(f"- **Exit Conditions:** {spec['exit_rules']}")
        lines.append(f"- **Stop Loss & Take Profit:** {spec['tp_sl']}")
        lines.append(f"- **Filters:** {spec['filters']}")
        lines.append(f"- **Position Sizing & Risk Scaling:** {spec['sizing_and_risk']}")
        lines.append(f"- **Venue Differences:** {spec['venue_differences']}")
        lines.append(f"- **Fees & Slippage Assumptions:** {spec['fees_slippage']}")
        lines.append("- **Config Rules Text:**")
        for rl in spec["config_rules"]:
            lines.append(f"  - {rl}")
        lines.append("")

    return "\n".join(lines)


def build_strategy_specs() -> list[dict]:
    specs: list[dict] = []
    for strategy in STRATEGIES:
        sid = strategy["id"]
        detail = STRATEGY_SPECS[sid]
        coin_parts = []
        config_rules = list(strategy.get("rules", []))
        for venue in VENUE_ORDER:
            if venue_enabled(strategy, venue):
                syms = [SYMBOLS[s]["label"] for s in symbols_for(sid, venue)]
                coin_parts.append(f"{VENUE_LABELS[venue]} ({len(syms)}): {', '.join(syms)}")
                resolved = resolve_strategy(sid, venue)
                for r in resolved["rules"]:
                    if r not in config_rules:
                        config_rules.append(f"[{VENUE_LABELS[venue]}] {r}")
        specs.append(
            {
                "id": sid,
                "label": strategy["label"],
                "coins_summary": " | ".join(coin_parts),
                "config_rules": config_rules,
                **detail,
            }
        )
    return specs


def build_report(
    rows: list[dict],
    window_label: str,
    balance: float,
    commission: float,
    slippage: float,
    daily_filter: bool,
    elapsed_s: float,
) -> dict:
    ok_rows = [r for r in rows if not r.get("error")]
    active = [r for r in ok_rows if r["stats"]["total_trades"] > 0]
    profitable = [r for r in active if r["stats"]["net_profit"] > 0]
    losing = [r for r in active if r["stats"]["net_profit"] < 0]
    flat = [r for r in ok_rows if r["stats"]["net_profit"] == 0]

    total_gp = sum(r["stats"]["gross_profit"] for r in ok_rows)
    total_gl = sum(r["stats"]["gross_loss"] for r in ok_rows)
    total_net = sum(r["stats"]["net_profit"] for r in ok_rows)
    total_trades = sum(r["stats"]["total_trades"] for r in ok_rows)
    total_wins = sum(r["stats"]["wins"] for r in ok_rows)
    total_comm = sum(r["stats"]["commission_paid"] for r in ok_rows)
    overall_pf = (total_gp / abs(total_gl)) if total_gl < 0 else (None if total_gp > 0 else 0.0)
    overall_wr = (total_wins / total_trades * 100.0) if total_trades else 0.0

    # Ranked by net_profit descending (errors at bottom)
    ranked = sorted(
        rows,
        key=lambda r: (0 if not r.get("error") else 1, -(r["stats"]["net_profit"] if not r.get("error") else -1e18)),
    )
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i

    # Venue aggregates
    venue_aggs: list[dict] = []
    for venue in VENUE_ORDER:
        vrows = [r for r in ok_rows if r["venue"] == venue]
        if not vrows:
            continue
        gp = sum(r["stats"]["gross_profit"] for r in vrows)
        gl = sum(r["stats"]["gross_loss"] for r in vrows)
        tr = sum(r["stats"]["total_trades"] for r in vrows)
        wn = sum(r["stats"]["wins"] for r in vrows)
        worst = max(vrows, key=lambda r: r["stats"]["max_drawdown"])
        venue_aggs.append(
            {
                "venue": venue,
                "venue_label": VENUE_LABELS[venue],
                "combos": len(vrows),
                "active": sum(1 for r in vrows if r["stats"]["total_trades"] > 0),
                "profitable": sum(1 for r in vrows if r["stats"]["net_profit"] > 0),
                "total_trades": tr,
                "long_trades": sum(r["stats"]["long_trades"] for r in vrows),
                "short_trades": sum(r["stats"]["short_trades"] for r in vrows),
                "wins": wn,
                "win_rate": (wn / tr * 100.0) if tr else 0.0,
                "gross_profit": gp,
                "gross_loss": gl,
                "profit_factor": (gp / abs(gl)) if gl < 0 else (None if gp > 0 else 0.0),
                "net_profit": sum(r["stats"]["net_profit"] for r in vrows),
                "avg_net_pct": sum(r["stats"]["net_profit_pct"] for r in vrows) / len(vrows),
                "worst_dd": worst["stats"]["max_drawdown"],
                "worst_dd_pct": worst["stats"]["max_drawdown_pct"],
                "commission_paid": sum(r["stats"]["commission_paid"] for r in vrows),
            }
        )

    # Per-strategy aggregates (by venue + strategy)
    strat_aggs: list[dict] = []
    for venue in VENUE_ORDER:
        for strategy in STRATEGIES:
            if not venue_enabled(strategy, venue):
                continue
            srows = [r for r in ok_rows if r["venue"] == venue and r["strategy_id"] == strategy["id"]]
            if not srows:
                continue
            gp = sum(r["stats"]["gross_profit"] for r in srows)
            gl = sum(r["stats"]["gross_loss"] for r in srows)
            tr = sum(r["stats"]["total_trades"] for r in srows)
            wn = sum(r["stats"]["wins"] for r in srows)
            net_sum = sum(r["stats"]["net_profit"] for r in srows)
            worst = max(srows, key=lambda r: r["stats"]["max_drawdown"])
            tims = [r["stats"]["time_in_market_pct"] for r in srows if r["stats"]["time_in_market_pct"] is not None]
            strat_aggs.append(
                {
                    "venue": venue,
                    "venue_label": VENUE_LABELS[venue],
                    "strategy_id": strategy["id"],
                    "strategy_label": strategy["label"],
                    "coins_tested": len(srows),
                    "active_coins": sum(1 for r in srows if r["stats"]["total_trades"] > 0),
                    "profitable_coins": sum(1 for r in srows if r["stats"]["net_profit"] > 0),
                    "total_trades": tr,
                    "long_trades": sum(r["stats"]["long_trades"] for r in srows),
                    "short_trades": sum(r["stats"]["short_trades"] for r in srows),
                    "win_rate": (wn / tr * 100.0) if tr else 0.0,
                    "profit_factor": (gp / abs(gl)) if gl < 0 else (None if gp > 0 else 0.0),
                    "total_net_profit": net_sum,
                    "avg_net_pct": sum(r["stats"]["net_profit_pct"] for r in srows) / len(srows),
                    "expectancy": (net_sum / tr) if tr else 0.0,
                    "worst_dd": worst["stats"]["max_drawdown"],
                    "worst_dd_pct": worst["stats"]["max_drawdown_pct"],
                    "avg_time_in_market_pct": (sum(tims) / len(tims)) if tims else None,
                    "commission_paid": sum(r["stats"]["commission_paid"] for r in srows),
                }
            )

    ok_ranked = [r for r in ranked if not r.get("error")]
    best_5 = ok_ranked[:5]
    worst_5 = list(reversed(ok_ranked[-5:])) if ok_ranked else []

    analysis = _build_analysis(rows, venue_aggs, strat_aggs, balance)
    strategy_specs = build_strategy_specs()

    report = {
        "window_label": window_label,
        "settings": {
            "balance": balance,
            "commission": commission,
            "slippage": slippage,
            "daily_filter": daily_filter,
            "internal_k": DEFAULT_K,
        },
        "summary": {
            "total_combos": len(rows),
            "completed_combos": len(ok_rows),
            "failed_combos": len(rows) - len(ok_rows),
            "active_combos": len(active),
            "profitable_combos": len(profitable),
            "losing_combos": len(losing),
            "flat_combos": len(flat),
            "total_net_profit": total_net,
            "total_trades": total_trades,
            "overall_win_rate": overall_wr,
            "overall_profit_factor": overall_pf,
            "total_commission": total_comm,
            "elapsed_s": elapsed_s,
            "best_combo": best_5[0] if best_5 else None,
            "worst_combo": worst_5[0] if worst_5 else None,
        },
        "analysis": analysis,
        "venue_aggregates": venue_aggs,
        "strategy_aggregates": strat_aggs,
        "ranked": ranked,
        "best_5": best_5,
        "worst_5": worst_5,
        "strategy_specs": strategy_specs,
    }
    report["report_md"] = _build_markdown(report)
    return report


def run_sweep(
    balance: float,
    commission: float,
    slippage: float,
    daily_filter: bool,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    period: str = DEFAULT_PERIOD,
) -> dict:
    """Execute all enabled venue/strategy/symbol combos sequentially and build the full report."""
    t0 = time.monotonic()
    canonical, exec_order = combo_plan()
    total = len(exec_order)
    window = period_window(period)
    window_label = ""
    results_by_idx: dict[int, dict] = {}

    for done_idx, item in enumerate(exec_order):
        if cancel_check and cancel_check():
            raise RuntimeError("Sweep cancelled by a newer request")
        label = f"{item['venue_label']} · {item['strategy_label']} · {item['symbol_label']}"
        if progress_cb:
            progress_cb(done_idx, total, label)
        try:
            res = run_backtest(
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
                window_label = res["window_label"]
            results_by_idx[item["order_idx"]] = {
                **item,
                "stats": res["stats"],
                "note": res.get("note", ""),
                "daily_filter": res["daily_filter"],
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            results_by_idx[item["order_idx"]] = {
                **item,
                "stats": None,
                "note": "",
                "daily_filter": daily_filter and item["venue"] == "pivex",
                "error": str(exc),
            }
        if (done_idx + 1) % 10 == 0:
            gc.collect()

    if progress_cb:
        progress_cb(total, total, "Building report…")

    ordered_rows = [results_by_idx[item["order_idx"]] for item in canonical]
    elapsed_s = time.monotonic() - t0
    return build_report(
        ordered_rows,
        window_label=window_label or f"Last {PERIODS[period]['label']}",
        balance=balance,
        commission=commission,
        slippage=slippage,
        daily_filter=daily_filter,
        elapsed_s=elapsed_s,
    )
