"""HAMA 1h + Reversal Turtle MTF 5m combo (Binance only).

Entries: official Reversal Turtle MTF 5m signals (js/v31/combo.js turtleSignals),
kept only when the last completed HAMA Stable 1h bar agrees (js/v31/combo_ht.js).
Fill at the next 5m open.

Exits, walked on 1m bars:
- Official Turtle stop: average entry -/+ atrd (about 23.3 x 5m ATR(14)), updated at
  the first minute of every 5m bar from the previous 5m bar's atrd.
- At +3% from entry, half the position closes and the stop on the rest moves to
  breakeven. The rest targets +6%. No +3% add.
- If a stop and a target trade in the same 1m bar, the stop fills first. A gap
  through a level fills at that minute's open.
- A position still open at the end of the window closes at the last 1m close.

Size is a fixed $10,000 book per trade (tester style, not compounded). The rows
returned look like run.js rows, one row per position: qty is the full entry,
exit is the quantity-weighted average of the partial and final exits, so the
tester's commission and slippage settle exactly on entry + exit notional.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import numpy as np

from .data import load_tf

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_JS = ROOT / "js" / "v31" / "combo_ht.js"
STRATEGY_ID = "combo_ht_5m"
BOOK = 10_000.0
ENGINE_FEE = 0.0004
PARTIAL_PCT = 0.03
TP_PCT = 0.06
M5 = 300_000


def _rows(bars) -> list[list[float]]:
    return [
        [int(t), float(o), float(h), float(l), float(c)]
        for t, o, h, l, c in zip(bars.open_time, bars.open, bars.high, bars.low, bars.close)
    ]


def signals(m5, h1, h4) -> dict:
    from .v31 import node_executable

    payload = {"m5": _rows(m5), "m1h": _rows(h1), "m4h": _rows(h4)}
    proc = subprocess.run(
        [node_executable(), str(SIGNALS_JS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=540,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "node failed").strip()
        raise RuntimeError(detail[-2000:])
    out = json.loads(proc.stdout)
    return {
        "t": np.asarray(out["t"], dtype=np.int64),
        "side": np.asarray(out["side"], dtype=np.int64),
        "raw": np.asarray(out["raw"], dtype=np.int64),
        "atrd": np.asarray([np.nan if v is None else float(v) for v in out["atrd"]], dtype=float),
    }


def _adverse(side: int, lo: float, hi: float, level: float) -> bool:
    return lo <= level if side == 1 else hi >= level


def _favor(side: int, lo: float, hi: float, level: float) -> bool:
    return hi >= level if side == 1 else lo <= level


def walk(sig: dict, m1, i: int, side: int, end_ms: int, book: float = BOOK) -> dict | None:
    """Walk one position from the 5m bar after signal bar i. None when it cannot open."""
    t5 = sig["t"]
    atrd = sig["atrd"]
    entry_ms = int(t5[i + 1])
    entry = float(sig["open5"][i + 1])
    ad0 = float(atrd[i])
    if not math.isfinite(entry) or entry <= 0 or not math.isfinite(ad0) or ad0 <= 0:
        return None
    stop = entry - side * ad0
    if (side == 1 and stop >= entry) or (side == -1 and stop <= entry):
        return None
    qty0 = book / entry
    qty = qty0
    tp = entry * (1 + side * TP_PCT)
    px3 = entry * (1 + side * PARTIAL_PCT)
    be = False
    closes: list[tuple[float, float, str]] = []  # qty, price, why
    reason = "END"
    exit_ms = entry_ms
    mae = mfe = 0.0
    mt, mo, mh, ml, mc = m1.open_time, m1.open, m1.high, m1.low, m1.close
    j = int(np.searchsorted(mt, entry_ms, side="left"))
    last5 = i + 1
    n1 = len(mt)
    while j < n1:
        t = int(mt[j])
        if t >= end_ms:
            break
        o, h, lo, c = float(mo[j]), float(mh[j]), float(ml[j]), float(mc[j])
        close_ms = t + 60_000
        b5 = t - (t % M5)
        k = int(np.searchsorted(t5, b5, side="left"))
        if k < len(t5) and int(t5[k]) == b5:
            last5 = k
        else:
            k = last5
        if t % M5 == 0 and not be and k >= 1:
            ad = float(atrd[k - 1])
            if not math.isfinite(ad) or ad <= 0:
                ad = ad0
            stop = entry - side * ad
        adv = lo if side == 1 else h
        fav = h if side == 1 else lo
        mae = max(mae, side * (entry - adv) / entry)
        mfe = max(mfe, side * (fav - entry) / entry)
        if _adverse(side, lo, h, stop):
            fill = o if _adverse(side, o, o, stop) else stop
            reason = "BE" if be else "SL"
            closes.append((qty, fill, reason))
            qty = 0.0
            exit_ms = close_ms
            break
        if _favor(side, lo, h, tp):
            fill = o if _favor(side, o, o, tp) else tp
            reason = "TP 6%"
            closes.append((qty, fill, reason))
            qty = 0.0
            exit_ms = close_ms
            break
        if not be and _favor(side, lo, h, px3):
            be = True
            fill3 = o if _favor(side, o, o, px3) else px3
            half = qty * 0.5
            closes.append((half, fill3, "½ at +3%"))
            qty -= half
            stop = entry
        exit_ms = close_ms
        j += 1
    if qty > 0:
        k = min(j, n1 - 1)
        while k >= 0 and int(mt[k]) >= end_ms:
            k -= 1
        if k >= 0:
            closes.append((qty, float(mc[k]), "end of test"))
            exit_ms = int(mt[k]) + 60_000
        else:
            closes.append((qty, entry, "end of test"))
            exit_ms = entry_ms
        reason = "end of test"
    gross = sum(q * (px - entry) * side for q, px, _ in closes)
    exit_notional = sum(q * px for q, px, _ in closes)
    exit_avg = exit_notional / qty0
    fee = ENGINE_FEE * (qty0 * entry + exit_notional)
    partial = any(why == "½ at +3%" for _, _, why in closes)
    if partial and reason == "BE":
        label = "½ +3% → BE"
    elif partial and reason == "TP 6%":
        label = "½ +3% → TP 6%"
    elif partial:
        label = f"½ +3% → {reason}"
    else:
        label = reason
    return {
        "side": side,
        "qty": qty0,
        "entry_ms": entry_ms,
        "exit_ms": exit_ms,
        "entry": entry,
        "exit": exit_avg,
        "stop": entry - side * ad0,
        "tp": tp,
        "entry_reason": "LONG" if side == 1 else "SHORT",
        "exit_reason": label,
        "gross": gross,
        "fee": fee,
        "net": gross - fee,
        "run_up": mfe * qty0 * entry,
        "drawdown": mae * qty0 * entry,
        "mult": 1,
        "tranche": None,
        "adjustable": True,
        "fills": [{"qty": q, "price": px, "why": why} for q, px, why in closes],
    }


def run_engine(symbol: str, warmup: int, start: int, end: int, skip_missing_days: bool = False) -> dict:
    m1 = load_tf(symbol, "1m", warmup, end, skip_missing_days=skip_missing_days)
    m5 = load_tf(symbol, "5m", warmup, end, skip_missing_days=skip_missing_days)
    h1 = load_tf(symbol, "1h", warmup, end, skip_missing_days=skip_missing_days)
    h4 = load_tf(symbol, "4h", warmup, end, skip_missing_days=skip_missing_days)
    sig = signals(m5, h1, h4)
    if len(sig["t"]) != len(m5) or not np.array_equal(sig["t"], m5.open_time):
        raise RuntimeError("combo signals are not aligned with the 5m bars")
    sig["open5"] = m5.open
    ct = sig["t"] + M5
    n = len(sig["t"])
    trades: list[dict] = []
    i = 1
    while i + 1 < n:
        if int(ct[i]) < start:
            i += 1
            continue
        if int(sig["t"][i + 1]) >= end:
            break
        side = int(sig["side"][i])
        if side == 0:
            i += 1
            continue
        tr = walk(sig, m1, i, side, end)
        if tr is None:
            i += 1
            continue
        trades.append(tr)
        last = tr["exit_ms"] - 60_000
        last -= last % M5
        j = int(np.searchsorted(sig["t"], last, side="left"))
        i = max(j, i) + 1
    note = (
        "Binance. Fixed $10,000 book per trade, not compounded. Official Turtle 5m entries only when the "
        "completed 1h HAMA trend agrees. Official Turtle stop, half off at +3% then breakeven on the rest, "
        "rest to 6%. No add. One row per trade; the exit price is the size-weighted average of both exits."
    )
    return {"trades": trades, "meta": {"note": note, "scale": 1}}
