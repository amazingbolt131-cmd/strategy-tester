"""Pivex dollar-risk settings.

Quantity is risk_usd / |entry - stop|, not a fixed notional. The amounts
below are the defaults and can be edited. The starting balance is only the
equity that return and drawdown percentages are measured against.
"""

from __future__ import annotations

SYMBOLS = {
    "BTCUSDT": {"label": "BTC", "step": 0.001},
    "ETHUSDT": {"label": "ETH", "step": 0.001},
    "SOLUSDT": {"label": "SOL", "step": 0.01},
    "BNBUSDT": {"label": "BNB", "step": 0.01},
    "XRPUSDT": {"label": "XRP", "step": 0.1},
    "DOGEUSDT": {"label": "DOGE", "step": 1.0},
    "ZECUSDT": {"label": "ZEC", "step": 0.001},
    "UNIUSDT": {"label": "UNI", "step": 1.0},
    "VETUSDT": {"label": "VET", "step": 1.0},
    "XMRUSDT": {"label": "XMR", "step": 0.001},
    "ARBUSDT": {"label": "ARB", "step": 0.1},
    "CRVUSDT": {"label": "CRV", "step": 0.1},
    "FILUSDT": {"label": "FIL", "step": 0.1},
    "KAITOUSDT": {"label": "KAITO", "step": 0.1},
    "TRUMPUSDT": {"label": "TRUMP", "step": 0.01},
    "1000PEPEUSDT": {"label": "1000PEPE", "step": 1.0},
    "BCHUSDT": {"label": "BCH", "step": 0.001},
    "ENAUSDT": {"label": "ENA", "step": 1.0},
    "LINKUSDT": {"label": "LINK", "step": 0.01},
    "NEARUSDT": {"label": "NEAR", "step": 1.0},
    "SUIUSDT": {"label": "SUI", "step": 1.0},
    "WLDUSDT": {"label": "WLD", "step": 1.0},
    # Extra tick sizes for the Ranking screen only. These are not added to
    # CORE_SYMBOLS or per-strategy coin lists, so Run/Start dropdowns stay the same.
    "LTCUSDT": {"label": "LTC", "step": 0.001},
    "HYPEUSDT": {"label": "HYPE", "step": 0.01},
    "BIGTIMEUSDT": {"label": "BIGTIME", "step": 1.0},
    "EVAAUSDT": {"label": "EVAA", "step": 0.1},
    "LISTAUSDT": {"label": "LISTA", "step": 1.0},
    "BLESSUSDT": {"label": "BLESS", "step": 1.0},
    "GUAUSDT": {"label": "GUA", "step": 1.0},
    "MMTUSDT": {"label": "MMT", "step": 1.0},
    "PAXGUSDT": {"label": "PAXG", "step": 0.001},
    "MANTRAUSDT": {"label": "MANTRA", "step": 1.0},
    "ADAUSDT": {"label": "ADA", "step": 1.0},
    "PIEVERSEUSDT": {"label": "PIEVERSE", "step": 1.0},
    "OPGUSDT": {"label": "OPG", "step": 1.0},
    "BTWUSDT": {"label": "BTW", "step": 1.0},
    "XAUTUSDT": {"label": "XAUT", "step": 0.001},
    "TAOUSDT": {"label": "TAO", "step": 0.001},
    "AAVEUSDT": {"label": "AAVE", "step": 0.1},
    "AVAXUSDT": {"label": "AVAX", "step": 1.0},
    "AIOTUSDT": {"label": "AIOT", "step": 1.0},
    "PUMPUSDT": {"label": "PUMP", "step": 1.0},
    "VVVUSDT": {"label": "VVV", "step": 0.01},
    "AKEUSDT": {"label": "AKE", "step": 1.0},
    "INJUSDT": {"label": "INJ", "step": 0.1},
    "BSVUSDT": {"label": "BSV", "step": 0.1},
    "DOTUSDT": {"label": "DOT", "step": 0.1},
    "DASHUSDT": {"label": "DASH", "step": 0.001},
    "ETCUSDT": {"label": "ETC", "step": 0.01},
    "THETAUSDT": {"label": "THETA", "step": 0.1},
    "TRXUSDT": {"label": "TRX", "step": 1.0},
    "XLMUSDT": {"label": "XLM", "step": 1.0},
    "XTZUSDT": {"label": "XTZ", "step": 0.1},
}

# Always offered, then each strategy adds its live coins.
CORE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
VENUE_ORDER = ("pivex", "binance")
VENUE_LABELS = {"pivex": "Pivex", "binance": "Binance"}

# Binance USDT-M taker is 0.04% per fill. Slippage is applied against the
# order (buys pay more, sells receive less).
DEFAULT_BALANCE = 10_000.0
DEFAULT_COMMISSION_PCT = 0.04
DEFAULT_SLIPPAGE_PCT = 0.0

# Warmup covers EMA200, the 25-day HAMA colour, and the Lorentzian lookback.
WARMUP_DAYS = 40
TEST_DAYS = 30

# Backtest period dropdown. Every period ends at the close of the last published
# UTC day; the start is that end minus the period.
PERIODS = {
    "1m": {"label": "1 month", "days": 30},
    "3m": {"label": "3 months", "days": 91},
    "6m": {"label": "6 months", "days": 182},
    "1y": {"label": "1 year", "days": 365},
    "3y": {"label": "3 years", "start": "2023-12-01"},
}
DEFAULT_PERIOD = "1m"

# Pivex combined-6 size is unit × weight × k. The live app defaults to k = 8.
DEFAULT_K = 8.0
K_MIN = 0.25
K_MAX = 50.0
DEFAULT_DAILY_FILTER = True

STRATEGIES = [
    {
        "id": "micro_1h",
        "binance": False,
        "label": "Pivex Micro · 1h",
        "group": "v31",
        "chart_tf": "1h",
        "risk": 125.0,
        "coins": {"pivex": ["XRPUSDT", "DOGEUSDT"], "binance": ["XRPUSDT", "DOGEUSDT"]},
        "rules": [
            "Seq5 on a closed 1h bar: EMA 21/55, Supertrend 2.5 ATR(10), RSI 14 versus 50, and a close back through the EMA20 ± ATR(10) band. At least 3 of the 4 quality points.",
            "Fill is the next hour's open. Stop is 1.5 × ATR(10) of the signal bar, target is 3 × ATR. On the 5m tape the stop is filled first if both sit in the same bar.",
            "Skip a long when the last closed BTC daily RSI(14) is below 30, and a short when it is above 70.",
            "One Micro trade at a time. Size is $125 of risk, including a 6 bp fee buffer, then × weight × k.",
        ],
    },
    {
        "id": "turtle_5m",
        "label": "Reversal Turtle MTF · 5m",
        "group": "v31",
        "chart_tf": "5m",
        "risk": 10000.0,
        "coins": {"pivex": [], "binance": []},
        "rules": [
            "30m Turtle bias from completed 30m buckets, session reset at 17:00 New York. Donchian 20/10, and the breakout has to hold a 2× ATR stop.",
            "5m trigger: close beyond SMA 200 and within 6% of the 20-bar extreme, in the direction of the previous closed 30m bias.",
            "Binance fills half at the next 5m open and adds the other half after +3%. TP1 at 6% closes only the first half. TP2 at 3% closes the add.",
            "Pivex fills the whole book at the next 5m open. One stop, one 6% target, no add. Notional is $10,000 × weight × k.",
        ],
    },
    {
        "id": "ml_5m",
        "label": "ML Lorentz · 5m",
        "group": "v31",
        "chart_tf": "5m",
        "risk": 10000.0,
        "coins": {"pivex": [], "binance": []},
        "rules": [
            "Lorentzian kNN on five features (RSI 14, WaveTrend 10/11, CCI 20, ADX 20, RSI 9), 2,000-bar lookback, 12 neighbours, every 4th bar.",
            "A volatility filter, a regime filter at −0.1, and a rational-quadratic kernel must agree. Short only: long signals are ignored.",
            "Fill is the next 5m open, one tick through the open. Stop 3%, target 3.1%, time stop at 300 bars. One position at a time.",
            "Notional starts at $10,000, then × weight × k.",
        ],
    },
    {
        "id": "hama_30m",
        "binance": False,
        "label": "HAMA 30m",
        "group": "v31",
        "chart_tf": "30m",
        "risk": 20.739227210345963,
        "max_step": 1,
        "loss_mode": "flat",
        "coins": {"pivex": ["ETHUSDT", "BNBUSDT"], "binance": ["ETHUSDT", "BNBUSDT"]},
        "rules": [
            "HAMA Stable on 30m: CO length 25 on the previous midpoint, CC length 20 on OHLC4, gap 0.3 × ATR(14), locked to the previous closed 1h HAMA colour.",
            "Long only while the 1h close is above its EMA 200, short only while it is below. Entry is the signal bar's close.",
            "Stop is the 10-bar swing, widened so the distance is at least 0.20 × ATR and $0.25 scaled by the SOL price.",
            "A stop is filled from the bar's high/low. A colour flip exits at that bar's close. Flat risk, then × weight × k. A loss does not add a step.",
        ],
    },
    {
        "id": "hama_1h",
        "binance": False,
        "label": "HAMA 1h (loss-step)",
        "group": "v31",
        "chart_tf": "1h",
        "risk": 7.592660371373892,
        "max_step": 8,
        "loss_mode": "step",
        "coins": {"pivex": ["ZECUSDT", "UNIUSDT"], "binance": ["ZECUSDT", "UNIUSDT"]},
        "rules": [
            "Same HAMA Stable rules on 1h, gap 0.3 × ATR(14), locked to the previous closed 4h colour, with the 1h EMA 200 side filter.",
            "The live app leaves this strategy off until its box is checked. It is selectable here.",
            "Risk starts at one step and adds a step after a loss, up to 8, then × weight × k. A win resets to step 1.",
        ],
    },
    {
        "id": "s5_15m",
        "label": "S5 15m (add, TP1/TP2)",
        "group": "v31",
        "chart_tf": "15m",
        "risk": 111.41632457034615,
        "loss_mode": "after",
        "loss_after": 2,
        "loss_mult": 2.0,
        "mult_cap": 2.0,
        "coins": {
            "pivex": ["VETUSDT", "XMRUSDT"],
            "binance": ["ARBUSDT", "CRVUSDT", "FILUSDT", "KAITOUSDT", "TRUMPUSDT"],
        },
        "venues": {
            "binance": {"risk": 22.085622688828757, "loss_mode": "double", "mult_cap": 5},
        },
        "rules": [
            "Extreme-reversal trigger on a closed 15m bar: close through the SMA 200 and within 6% of the 20-bar extreme, only when the previous 30m Turtle bias agrees. The bias locks on the New York session that rolls at 17:00.",
            "Fill is the next 1m open. Stop distance is the average of the last 1,000 trigger ATRs (or the current ATR) times 70/3.",
            "Binance adds once after a 3% move in favour. TP1 at 6% closes only the first tranche. TP2 at 3% closes the add. Pivex does not add: the 6% target closes the single entry.",
            "Pivex risk is the $111.42 unit × weight × k, then 2× after two losing baskets, cap 2×. Binance risk is $22.09, doubled after each loss, cap 5×, and is not scaled by k.",
        ],
    },
    {
        "id": "sol1m_morning",
        "binance": False,
        "label": "SOL 1m Morning HAMA",
        "group": "v31",
        "chart_tf": "15m",
        "risk": 100.0,
        "coins": {"pivex": [], "binance": []},
        "rules": [
            "At 08:00 Kyiv, the move from the previous 23:59 close to the 07:59 close. +1% to +3% is a long-only day, −1% to −3% is a short-only day, anything else is skipped.",
            "From 08:00 to midnight Kyiv, 1m HAMA flips (CO 25, CC 20, ATR 10, gap 0.3 × ATR) that match the previous completed 3m colour and the day's direction.",
            "Entry is that 1m close plus a $0.06 spread. Notional is a fixed $40,000. Stop is −$100. There is no target.",
            "The first losing trade ends the day. A win allows the next same-direction flip. The day is also stopped if open P&L would take it to −$600. Not part of the combo, and not scaled by k.",
        ],
    },
    {
        "id": "dg_grid",
        "label": "DG 1h/1m grid",
        "group": "v31",
        "chart_tf": "15m",
        "pivex": False,
        "binance": True,
        "risk": 1000.0,
        "loss_mode": "flat",
        "coins": {"pivex": [], "binance": ["SOLUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT"]},
        "rules": [
            "Directional unload grid. 1h colour is EMA 200 plus Turtle 20/10 plus HAMA Stable, and the 4h HAMA colour has to agree. Fills are on 1m.",
            "Flat $1,000 notional per level, up to 8 levels, added every 0.5% further in favour. No step-up after a loss.",
            "A 1% pullback unloads only the newest level. A 10% move against the average closes the basket. An opposite colour closes it too.",
            "Pivex does not run this grid.",
        ],
    },
    {
        "id": "er_grid",
        "label": "ER 15m/1m grid",
        "group": "v31",
        "chart_tf": "15m",
        "pivex": False,
        "binance": True,
        "risk": 0.36023713902975735,
        "loss_mode": "step",
        "max_step": 10,
        "coins": {"pivex": [], "binance": ["1000PEPEUSDT", "BCHUSDT", "BNBUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "LINKUSDT", "NEARUSDT", "SUIUSDT", "WLDUSDT", "ZECUSDT"]},
        "rules": [
            "Extreme reversal on 15m (Bollinger length 20 with deviation 0, proximity 50%, ATR multiple 9) and a 1m grid.",
            "Adds use the thirds-carry schedule, step 0.20%, reversal 0.10%, up to 5 units, target 6%. Direction is opposite the signal.",
            "Each unit is $0.360. A losing basket adds one step, up to 10. A win resets to step 1.",
        ],
    },
    {
        "id": "hama_grid_5m",
        "label": "HAMA Grid+Count 5m",
        "group": "v31",
        "chart_tf": "5m",
        "pivex": False,
        "binance": True,
        "risk": 1000.0,
        "coins": {"pivex": [], "binance": ["BNBUSDT", "XRPUSDT"]},
        "rules": [
            "5m HAMA Stable, gap 0.3 × ATR(14). The colour-count filter reads closed 1h candles over 3 days and needs a 65% share. Neutral allows both sides.",
            "Adds every 0.75% against the flip, up to 5 × $1,000. Target 6% from the flip close, stop 4.5%.",
        ],
    },
    {
        "id": "hama_grid_1m",
        "label": "HAMA Grid+Count 1m",
        "group": "v31",
        "chart_tf": "15m",
        "pivex": False,
        "binance": True,
        "risk": 1000.0,
        "coins": {"pivex": [], "binance": ["BNBUSDT", "XRPUSDT"]},
        "rules": [
            "1m HAMA Stable with a 15m colour lock. The count filter reads closed 4h candles over 5 days at a 65% share.",
            "Adds every 0.30% against the flip, up to 5 × $1,000. Target 6% from the flip close, stop 1.8%.",
        ],
    },
]

# Strategies offered only on the Single run tab. They are not part of the Start
# sweep or the Ranking board, so those plans and the precomputed ranking stay as they are.
TESTER_EXTRA_STRATEGIES = [
    {
        "id": "combo_ht_5m",
        "label": "HAMA 1h + Turtle 5m Combo",
        "group": "combo",
        "chart_tf": "5m",
        "pivex": False,
        "binance": True,
        "risk": 10000.0,
        "coins": {"pivex": [], "binance": []},
        "rules": [
            "Entry: official Reversal Turtle MTF 5m signal (30m session bias, 5m close beyond SMA 200 within 6% of the 20-bar extreme), filled at the next 5m open.",
            "Trend filter: the last completed 1h HAMA Stable bar (CO 25 / CC 20, gap 0.3 × ATR(14), 4h colour lock) must be on the same side and agree with the 1h EMA 200. Nothing from an unfinished bar is used.",
            "Stop: official Turtle stop, average entry ± atrd (about 23.3 × 5m ATR(14)), updated every 5m bar until breakeven.",
            "At +3% half the position closes and the stop on the rest moves to breakeven. The rest targets +6%. No +3% add.",
            "Exits are walked on 1m bars. If a stop and a target trade in the same minute, the stop fills first.",
            "Fixed $10,000 book per trade, not compounded. Binance only.",
        ],
    },
    {
        "id": "hama_ladder_1m",
        "label": "HAMA Scale-in Ladder",
        "group": "research",
        "chart_tf": "1m",
        "pivex": False,
        "binance": True,
        "risk": 100.0,
        "coins": {"pivex": [], "binance": ["BNBUSDT", "XRPUSDT"]},
        "rules": [
            "1m stable-HAMA flip, no higher-timeframe bias.",
            "Add on every 2nd 1m flip during the opposing 15m HAMA run.",
            "Geometric sizing 1, 2, 4, …, 128 over a maximum of 8 units (weights normalised to a $100 basket).",
            "5% basket stop. Fees 0.04%/side already deducted.",
            "Precomputed for 1 year (2025-11-27 → 2026-11-27) and 3 years (2023-12-01 → now) on BTC, ETH, SOL, BNB and XRP. P&L % is the sum of per-trade % of a $100 unit (1% = $1), not the return on the starting-balance field.",
        ],
    },
    {
        "id": "aw_scalper_1h",
        "label": "1h Alpha Wave Scalper + HAMA",
        "group": "research",
        "chart_tf": "1h",
        "pivex": False,
        "binance": True,
        "risk": 100.0,
        "coins": {"pivex": [], "binance": ["BNBUSDT", "XRPUSDT"]},
        "rules": [
            "1h Alpha Wave Scalper entries only when 1h HAMA Stable agrees.",
            "Exit: Inversed Channel at ATR(22) × 3. Fill at the 1h close.",
            "Fees 0.04%/side already deducted. $100 unit.",
            "Precomputed for 1 year and 3 years on BTC, ETH, SOL, BNB and XRP. P&L % is the sum of per-trade % of a $100 unit (1% = $1).",
        ],
    },
    {
        "id": "dg_grid_flow",
        "label": "DG Grid + flow",
        "group": "research",
        "chart_tf": "15m",
        "pivex": False,
        "binance": True,
        "risk": 100.0,
        "coins": {"pivex": [], "binance": ["BNBUSDT", "XRPUSDT"]},
        "rules": [
            "15m entry: 5 consecutive opposite raw HAMA candles plus taker-flow agreement.",
            "Grid adds every +0.5%, closes a unit on a 1% pullback, maximum 8 units (mode C).",
            "Full period, no last-third cut. Fees 0.04%/side already deducted.",
            "Precomputed for 1 year and 3 years on BTC, ETH, SOL, BNB and XRP. P&L % is the sum of per-trade % of a $100 unit (1% = $1).",
        ],
    },
]

TESTER_STRATEGIES = STRATEGIES + TESTER_EXTRA_STRATEGIES

STRATEGY_BY_ID = {s["id"]: s for s in TESTER_STRATEGIES}


def venue_enabled(strategy: dict, venue: str) -> bool:
    if strategy.get(venue) is False:
        return False
    return venue in VENUE_LABELS


def symbols_for(strategy_id: str, venue: str) -> list[str]:
    strategy = STRATEGY_BY_ID[strategy_id]
    extra = list(strategy.get("coins", {}).get(venue, []))
    ordered: list[str] = []
    for symbol in list(CORE_SYMBOLS) + extra:
        if symbol not in ordered and symbol in SYMBOLS:
            ordered.append(symbol)
    return ordered


def resolve_strategy(strategy_id: str, venue: str) -> dict:
    """Copy a strategy and apply that venue's risk card."""
    if strategy_id not in STRATEGY_BY_ID:
        raise ValueError(f"Unknown strategy {strategy_id}")
    if venue not in VENUE_LABELS:
        raise ValueError(f"Unknown venue {venue}")
    base = dict(STRATEGY_BY_ID[strategy_id])
    if not venue_enabled(base, venue):
        other = "Pivex" if venue_enabled(base, "pivex") else "Binance"
        raise ValueError(f"{base['label']} runs on {other} only")
    override = dict(base.get("venues", {}).get(venue, {}))
    base.update(override)
    base["venue"] = venue
    base["venue_label"] = VENUE_LABELS[venue]
    rules = list(base.get("rules", []))
    extra = _venue_rule(base)
    if extra:
        rules.append(extra)
    if venue == "pivex":
        rules.append("Pivex is single-entry: one full-size fill, one stop and one target. Adds and extra levels stay off. A partial close is still a close.")
    elif base["id"] in ("s5_15m", "turtle_5m", "dg_grid", "er_grid", "hama_grid_5m", "hama_grid_1m"):
        rules.append("Binance keeps the full multi-entry logic: adds and grid levels.")
    base["rules"] = rules
    return base


def _venue_rule(cfg: dict) -> str | None:
    if "loss_mode" not in cfg:
        return None
    label = cfg["venue_label"]
    risk = float(cfg["risk"])
    mode = cfg["loss_mode"]
    if mode == "step":
        cap = int(cfg["max_step"])
        return f"{label}: ${risk:.2f} at step 1, +1 step per losing trade, cap {cap} steps (${risk * cap:.2f}). A win resets to step 1."
    if mode == "after":
        return (
            f"{label}: ${risk:.2f} risk, {float(cfg['loss_mult']):.0f}× after "
            f"{int(cfg['loss_after'])} losing baskets, cap {float(cfg['mult_cap']):.0f}×. A win resets it."
        )
    if mode == "double":
        return f"{label}: ${risk:.2f} risk, doubles after each losing basket, cap {float(cfg['mult_cap']):.0f}×. A win resets it."
    if mode == "ladder":
        return (
            f"{label}: ${risk:.2f} per level, +1× every {int(cfg['loss_every'])} losing baskets, "
            f"cap {int(cfg['max_mult'])}×. A win resets it."
        )
    return f"{label}: flat ${risk:.2f} risk. Contracts = risk / |entry − stop|."


def catalog() -> dict:
    """What the symbol and venue dropdowns are allowed to show."""
    out = {}
    for strategy in TESTER_STRATEGIES:
        venues = {}
        for venue in VENUE_ORDER:
            enabled = venue_enabled(strategy, venue)
            venues[venue] = {
                "enabled": enabled,
                "label": VENUE_LABELS[venue],
                "symbols": [
                    {"id": symbol, "label": SYMBOLS[symbol]["label"]}
                    for symbol in symbols_for(strategy["id"], venue)
                ] if enabled else [],
            }
        out[strategy["id"]] = {"venues": venues}
    return out
