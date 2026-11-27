#!/usr/bin/env python3
"""Precompute 1y and 3y results for the three research strategies.

Uses the exact unfiltered engines from /workspace/bidask/research
(ladder, 1h AW Scalper+HAMA+InvCh x3, DG grid+flow). Fees already
in the nets (0.04%/side). $100 unit. 5 coins.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "/workspace/bidask")
sys.path.insert(0, "/workspace/bidask/research")
sys.path.insert(0, "/workspace/bidask/research/hist")

import aw_study as S
import exhaust as X
import grid_study as G
import ema200_test as E

ROOT = Path("/workspace/bidask/research/hist")
OUT = Path("/workspace/st-deploy/data/research_strats.json")
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
KYIV = timezone(timedelta(hours=2))
UTC = timezone.utc

# User-requested windows. Ends are exclusive for filtering; 3y uses all
# available bars through "now" so unfiltered totals match ema200_report.txt.
Y1_START = int(datetime(2025, 11, 27, tzinfo=UTC).timestamp() * 1000)
Y1_END_LABEL = int(datetime(2026, 11, 27, tzinfo=UTC).timestamp() * 1000)
# include 27 Nov 2026 partial day (data through ~07:19 UTC)
Y3_START = int(datetime(2023, 12, 1, tzinfo=UTC).timestamp() * 1000)

IDS = {
    "hama_ladder_1m": "HAMA Scale-in Ladder",
    "aw_scalper_1h": "1h Alpha Wave Scalper + HAMA",
    "dg_grid_flow": "DG Grid + flow",
}


def kyiv_month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, KYIV).strftime("%Y-%m")


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def price_at(M, t_ms, offset_ms=0):
    """Close of the bar whose open is t_ms - offset (fill at close)."""
    t = int(t_ms) - int(offset_ms)
    i = int(np.searchsorted(M["T"], t, side="left"))
    if i >= M["n"]:
        i = M["n"] - 1
    if i < 0:
        return None
    # prefer exact open match
    if i < M["n"] and int(M["T"][i]) == t:
        return float(M["C"][i])
    if i > 0 and int(M["T"][i - 1]) == t:
        return float(M["C"][i - 1])
    return float(M["C"][min(i, M["n"] - 1)])


def enrich(trs, M, fill_offset_ms, px_M=None):
    src = px_M or M
    out = []
    for t in trs:
        entry = price_at(src, t["t_entry"], fill_offset_ms)
        exit_px = price_at(src, t["t_exit"], 0)
        # t_exit from ladder is the timestamp of the exit bar (open time)
        if exit_px is None and "T" in src:
            j = int(np.searchsorted(src["T"], int(t["t_exit"]), side="left"))
            j = min(max(j, 0), src["n"] - 1)
            exit_px = float(src["C"][j])
        rec = dict(t)
        rec["entry"] = entry
        rec["exit"] = exit_px
        out.append(rec)
    return out


def stats_of(trs):
    if not trs:
        return dict(
            n=0, tot=0.0, dd=0.0, win=0.0, pf=None, gross_profit=0.0,
            gross_loss=0.0, avg=0.0, months={},
        )
    trs = sorted(trs, key=lambda t: (t["t_exit"], t["t_entry"]))
    nets = np.array([t["net"] for t in trs], float)
    eq = np.cumsum(nets)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    wins = nets[nets > 0]
    losses = nets[nets < 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float(losses.sum()) if len(losses) else 0.0
    pf = None
    if gl < 0:
        pf = (gp / abs(gl)) if gp else 0.0
    elif gp > 0:
        pf = None  # infinite
    by_m = defaultdict(lambda: [0.0, 0])
    for t in trs:
        mk = kyiv_month(t["t_exit"])
        by_m[mk][0] += t["net"]
        by_m[mk][1] += 1
    months = {mk: {"pnl": round(v[0], 6), "n": int(v[1])} for mk, v in sorted(by_m.items())}
    return dict(
        n=int(len(trs)),
        tot=float(nets.sum()),
        dd=dd,
        win=100.0 * float((nets > 0).mean()),
        pf=pf,
        gross_profit=gp,
        gross_loss=gl,
        avg=float(nets.mean()),
        months=months,
    )


def in_window(t, start, end):
    return start <= int(t["t_entry"]) < end


def pack_trades(trs):
    out = []
    for t in trs:
        out.append({
            "t_entry": int(t["t_entry"]),
            "t_exit": int(t["t_exit"]),
            "side": int(t["side"]),
            "net": float(t["net"]),
            "entry": None if t.get("entry") is None else float(t["entry"]),
            "exit": None if t.get("exit") is None else float(t["exit"]),
        })
    return out


def run_grid_on(a, start_ms, end_ms):
    """Full-period grid, no last-third cut. 15m exhaust+flow, C +0.5%/pb 1%/max 8."""
    a15 = E.agg_np(a, 15)
    bars = [
        dict(open_ms=int(r["T"]), o=float(r["O"]), h=float(r["H"]),
             l=float(r["L"]), c=float(r["C"]), v=float(r["V"]), tbv=float(r["TBV"]))
        for r in a15
    ]
    M = X.build("sym", 15, False, bars=bars)
    sig = [(i, sd) for i, sd in X.signals(M, "exh", 5, True) if i < M["n"] - 2]
    busy = -1
    tr = []
    for i, sd in sig:
        if i < busy:
            continue
        t_entry = int(M["T"][i]) + 15 * 60_000
        if t_entry < start_ms or t_entry >= end_ms:
            continue
        pnl, j = G.run_trade(M, i, sd, 5, "C", {"S": 0.5, "P": 1, "max": 8})
        busy = j
        tr.append(dict(
            t_entry=t_entry,
            t_exit=int(M["T"][j]),
            side=int(sd),
            net=float(pnl),
            entry=float(M["C"][i]),
            exit=float(M["C"][j]),
        ))
    return tr


def main():
    t0 = time.time()
    by_sym = {sid: {} for sid in IDS}
    data_end = {}

    for sym in SYMS:
        print(f"== {sym} ==", flush=True)
        a = E.load_npz(sym, "1m")
        data_end[sym] = int(a["T"][-1]) + 60_000  # close of last 1m bar
        t0s = time.time()
        M = E.prep_1m(a)
        print(f"    prep {time.time()-t0s:.1f}s", flush=True)
        a60, st60 = M["_1h"]

        t0s = time.time()
        lad = enrich(E.run_ladder(M), M, 60_000)
        print(f"    ladder {len(lad)} {time.time()-t0s:.1f}s", flush=True)

        t0s = time.time()
        aw = enrich(E.run_aw(M, a60, st60), M, 60 * 60_000, px_M=dict(T=a60["T"], C=a60["C"], n=len(a60)))
        # AW fill at 1h close; prices from 1h bars
        print(f"    aw {len(aw)} {time.time()-t0s:.1f}s", flush=True)

        t0s = time.time()
        # grid over the full available range; window filter applied later
        end_all = data_end[sym]
        grd = run_grid_on(a, Y3_START, end_all + 1)
        print(f"    grid {len(grd)} {time.time()-t0s:.1f}s", flush=True)

        by_sym["hama_ladder_1m"][sym] = lad
        by_sym["aw_scalper_1h"][sym] = aw
        by_sym["dg_grid_flow"][sym] = grd

    end_ms = max(data_end.values())
    windows = {
        "1y": {
            "label": "1 year",
            "start_ms": Y1_START,
            "end_ms": max(Y1_END_LABEL, end_ms),
            "window_label": f"{iso_utc(Y1_START)} → {iso_utc(max(Y1_END_LABEL, end_ms))} UTC",
        },
        "3y": {
            "label": "3 years",
            "start_ms": Y3_START,
            "end_ms": end_ms,
            "window_label": f"{iso_utc(Y3_START)} → {iso_utc(end_ms)} UTC",
        },
    }

    report = {
        "generated_at": datetime.now(KYIV).strftime("%Y-%m-%d %H:%M Europe/Kiev"),
        "unit_usd": 100.0,
        "commission_pct_side": 0.04,
        "coins": SYMS,
        "note": (
            "Fees of 0.04%/side are already in net P&L. Totals are the sum of "
            "per-trade % of a $100 unit (so 1% = $1). Max DD is the peak-to-trough "
            "of that cumulative % (same definition as the 3-year research report). "
            "Monthly buckets use exit time in Europe/Kiev (UTC+2)."
        ),
        "strategies": {
            "hama_ladder_1m": {
                "label": IDS["hama_ladder_1m"],
                "rules": [
                    "1m stable-HAMA flip, no higher-timeframe bias.",
                    "Add on every 2nd 1m flip (k2) during the opposing 15m HAMA run.",
                    "Geometric sizing 1, 2, 4, …, 128 over a maximum of 8 units (weights normalised to a $100 basket).",
                    "5% basket stop. Fees 0.04%/side already deducted.",
                ],
                "signal_tf": "1m",
                "tp": "None (held through the 15m run / next flip)",
                "sl": "5% basket stop",
                "indicators": "HAMA Stable 1m + 15m colour",
            },
            "aw_scalper_1h": {
                "label": IDS["aw_scalper_1h"],
                "rules": [
                    "1h Alpha Wave Scalper entries only when 1h HAMA Stable agrees.",
                    "Exit: Inversed Channel at ATR(22) × 3.",
                    "Fill at the 1h close. Fees 0.04%/side already deducted.",
                ],
                "signal_tf": "1h",
                "tp": "Inversed Channel (ATR × 3)",
                "sl": "Alpha Wave ATR(22) × 3 trail (Inversed Channel)",
                "indicators": "Alpha Wave Scalper + HAMA Stable 1h",
            },
            "dg_grid_flow": {
                "label": IDS["dg_grid_flow"],
                "rules": [
                    "15m entry: 5 consecutive opposite raw HAMA candles plus taker-flow agreement.",
                    "Grid adds every +0.5%, closes a unit on a 1% pullback, maximum 8 units at $50 each (mode C).",
                    "Full period, no last-third cut. Fees 0.04%/side already deducted.",
                ],
                "signal_tf": "15m",
                "tp": "1% pullback close of the newest unit",
                "sl": "10% of average entry (mode C basket stop)",
                "indicators": "15m HAMA Stable + 5-candle exhaust + taker delta",
            },
        },
        "windows": {},
    }

    for wid, wmeta in windows.items():
        w_start, w_end = wmeta["start_ms"], wmeta["end_ms"]
        block = {**wmeta, "strategies": {}}
        for sid in IDS:
            per = {}
            all_tr = []
            for sym in SYMS:
                trs = [t for t in by_sym[sid][sym] if in_window(t, w_start, w_end)]
                # For 3y ladder/AW, do not drop any research trade with entry >= data start
                # after the engine's own warmup — already the case.
                st = stats_of(trs)
                per[sym] = {"stats": st, "trades": pack_trades(trs)}
                all_tr.extend(trs)
            comb = stats_of(all_tr)
            block["strategies"][sid] = {
                "per_symbol": {sym: {"stats": per[sym]["stats"], "trades": per[sym]["trades"]} for sym in SYMS},
                "combined": {"stats": comb, "n_symbols": 5},
            }
            print(
                f"{wid} {sid}: combined tot={comb['tot']:+.1f}% n={comb['n']} dd={comb['dd']:.1f} win={comb['win']:.1f}% pf={comb['pf']}",
                flush=True,
            )
        report["windows"][wid] = block

    # Sanity vs ema200_report unfiltered
    lad3 = report["windows"]["3y"]["strategies"]["hama_ladder_1m"]["combined"]["stats"]
    aw3 = report["windows"]["3y"]["strategies"]["aw_scalper_1h"]["combined"]["stats"]
    report["sanity"] = {
        "ladder_3y_tot": lad3["tot"],
        "ladder_3y_n": lad3["n"],
        "ladder_3y_expected_tot": -224.2,
        "ladder_3y_expected_n": 5282,
        "aw_3y_tot": aw3["tot"],
        "aw_3y_n": aw3["n"],
        "aw_3y_expected_tot": 83.7,
        "aw_3y_expected_n": 736,
        "ladder_ok": abs(lad3["tot"] - (-224.2)) < 0.15 and lad3["n"] == 5282,
        "aw_ok": abs(aw3["tot"] - 83.7) < 0.15 and aw3["n"] == 736,
    }
    print("SANITY", report["sanity"], flush=True)
    print(f"elapsed {time.time()-t0:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # trades can be ~5k * 5 * 2 windows — fine as JSON
    OUT.write_text(json.dumps(report))
    print("wrote", OUT, "bytes", OUT.stat().st_size, flush=True)


if __name__ == "__main__":
    main()
