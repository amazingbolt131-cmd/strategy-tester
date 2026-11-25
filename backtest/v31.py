"""Run the extracted v3.1 engines.

The signal math lives in js/v31 and is executed with Node. Python loads the
futures tape, applies the requested commission and slippage, and turns the
engine's fills into the trade book the page renders.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .config import DEFAULT_K
from .data import load_tf

ROOT = Path(__file__).resolve().parent.parent
RUN_JS = ROOT / "js" / "v31" / "run.js"
ENGINE_FEE = 0.0004


def node_executable() -> str:
    """Prefer the Node binary unpacked by scripts/install-node.sh on Render."""
    bundled = ROOT / ".node" / "bin" / "node"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return "node"

# Pivex combined-6 weights. Live size = unit × weight × k. Binance is not scaled.
WEIGHTS = {
    "Micro": 0.8340981490184549,
    "Turtle": 0.15432856549524146,
    "ML": 0.10408505559108173,
    "HAMA30": 0.8652060249238318,
    "HAMA1h": 1.57006260138179,
    "S5": 1.1153394448639238,
}
UNIT = {
    "Micro": 125.0,
    "Turtle": 10000.0,
    "ML": 10000.0,
    "HAMA30": 20.739227210345963,
    "HAMA1h": 7.592660371373892,
    "S5": 111.41632457034615,
}


def pivex_size(key: str, k: float) -> float:
    """Unit × weight × k. Turtle's figure is the full book; the first fill is half."""
    return UNIT[key] * WEIGHTS[key] * k


def _rows(bars) -> list[list[float]]:
    return [
        [int(t), float(o), float(h), float(l), float(c)]
        for t, o, h, l, c in zip(bars.open_time, bars.open, bars.high, bars.low, bars.close)
    ]


def _series(strategy_id: str, symbol: str, warmup: int, end: int) -> dict:
    def tf(sym: str, name: str):
        return _rows(load_tf(sym, name, warmup, end))

    series: dict = {}
    if strategy_id == "hama_30m":
        series["chart"] = tf(symbol, "30m")
        series["chart_ms"] = 1_800_000
        series["htf"] = tf(symbol, "1h")
        series["htf_ms"] = 3_600_000
        series["h1"] = series["htf"]
        series["h1_ms"] = 3_600_000
    elif strategy_id == "hama_1h":
        series["chart"] = tf(symbol, "1h")
        series["chart_ms"] = 3_600_000
        series["htf"] = tf(symbol, "4h")
        series["htf_ms"] = 14_400_000
        series["h1"] = series["chart"]
        series["h1_ms"] = 3_600_000
    elif strategy_id == "s5_15m":
        series["m15"] = tf(symbol, "15m")
        series["m1"] = tf(symbol, "1m")
    elif strategy_id == "er_grid":
        series["m15"] = tf(symbol, "15m")
        series["m1"] = tf(symbol, "1m")
    elif strategy_id == "dg_grid":
        series["h1"] = tf(symbol, "1h")
        series["m1"] = tf(symbol, "1m")
    elif strategy_id in ("hama_grid_5m", "hama_grid_1m", "sol1m_morning"):
        series["m1"] = tf(symbol, "1m")
    elif strategy_id == "micro_1h":
        series["h1"] = tf(symbol, "1h")
        series["m5"] = tf(symbol, "5m")
        series["btc_daily"] = tf("BTCUSDT", "1d")
    elif strategy_id in ("turtle_5m", "ml_5m"):
        series["m5"] = tf(symbol, "5m")
    else:
        raise ValueError(f"Unknown strategy {strategy_id}")

    if strategy_id in ("hama_30m", "hama_1h", "s5_15m", "micro_1h", "turtle_5m", "ml_5m"):
        series["daily"] = tf(symbol, "1d")
    if strategy_id in ("hama_30m", "hama_1h") and symbol != "SOLUSDT":
        sol = load_tf("SOLUSDT", "1h", warmup, end)
        series["sol"] = [[int(t) + 3_600_000, float(c)] for t, c in zip(sol.open_time, sol.close)]
    return series


def run_engine(
    strategy_id: str,
    symbol: str,
    venue: str,
    warmup: int,
    start: int,
    end: int,
    k: float = DEFAULT_K,
    daily_filter: bool = True,
    series: dict | None = None,
    profit_target: float | None = None,
) -> dict:
    payload = {
        "id": strategy_id,
        "symbol": symbol,
        "venue": venue,
        "start": int(start),
        "end": int(end),
        "k": float(k),
        "daily_filter": bool(daily_filter) and venue == "pivex",
        "series": series if series is not None else _series(strategy_id, symbol, warmup, end),
    }
    if profit_target is not None:
        payload["profit_target"] = float(profit_target)
    proc = subprocess.run(
        [node_executable(), str(RUN_JS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=540,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "node failed").strip()
        raise RuntimeError(detail[-2000:])
    return json.loads(proc.stdout)


def settle(trade: dict, commission: float, slippage: float) -> tuple[float, float]:
    """Return net pnl and the commission charged.

    The engines already charge 0.04% a side (the morning strategy charges a
    $0.06 spread instead). A different commission, or any slippage, is applied
    on top of the engine's gross result.
    """
    gross = float(trade["gross"])
    fee = float(trade["fee"])
    net = float(trade["net"])
    if not trade.get("adjustable", True):
        if slippage:
            notional = abs(float(trade["qty"])) * (abs(float(trade["entry"])) + abs(float(trade["exit"])))
            net -= notional * slippage
        return net, fee
    if abs(commission - ENGINE_FEE) < 1e-12 and slippage == 0:
        return net, fee
    notional = abs(float(trade["qty"])) * (abs(float(trade["entry"])) + abs(float(trade["exit"])))
    new_fee = notional * commission
    return gross - new_fee - notional * slippage, new_fee
