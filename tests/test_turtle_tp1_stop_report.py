"""TP1-then-stop fixture through the tester's normal reporting path.

Engine fills come from the patched combo.js; trade rows, fees, and CSV columns
are produced by the same functions the app uses: run.js turtle() sizeNorm/FEE
loop (tests/turtle_tp1_stop_via_run.js, copied from js/v31/run.js), then
backtest.v31.settle, backtest.runner._book_from_engine, summarize, and app._csv.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from backtest.runner import _book_from_engine, _iso
from backtest.stats import summarize
from backtest.v31 import ENGINE_FEE, settle

ROOT = Path(__file__).resolve().parents[1]
FEE_PCT = 0.04  # tester default commission, already the engine's 0.04%/side


def _engine_trades():
    proc = subprocess.run(
        ["node", str(ROOT / "tests" / "turtle_tp1_stop_via_run.js")],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _report_rows(engine):
    start = int(engine["meta"]["start"])
    end = int(engine["meta"]["end"])
    commission = ENGINE_FEE
    book = _book_from_engine(engine["trades"], 10_000.0, commission, 0.0, start)
    book.finish()
    stats = summarize(book, 10_000.0, start_ms=start, end_ms=end)
    trades = []
    for i, trade in enumerate(book.trades, start=1):
        trades.append({
            "n": i,
            "side": "Long" if trade.side == 1 else "Short",
            "entry_time": _iso(trade.entry_time),
            "entry_price": trade.entry_price,
            "exit_time": _iso(trade.exit_time),
            "exit_price": trade.exit_price,
            "reason": f"{trade.entry_reason} → {trade.exit_reason}",
            "qty": trade.qty,
            "pnl": trade.pnl,
            "pnl_pct": trade.pnl_pct,
            "cumulative": trade.cumulative,
            "run_up": trade.run_up,
            "drawdown": trade.drawdown,
            "commission": trade.commission,
            "exit_reason": trade.exit_reason,
            "entry_reason": trade.entry_reason,
        })
    return {"stats": stats, "trades": trades, "engine": engine}


def test_tp1_then_stop_emits_one_tp1_and_one_stop_with_fees_on_actual_qty(tmp_path):
    engine = _engine_trades()
    raw = engine["trades"]
    assert len(raw) == 2
    reasons = [t["exit_reason"] for t in raw]
    assert reasons == ["TP1", "Stop"]
    assert [t["entry_reason"] for t in raw] == ["LONG", "LONG_ADD"]
    assert [t["tranche"] for t in raw] == [1, 2]

    q1, q2 = raw[0]["qty"], raw[1]["qty"]
    assert abs(q1 - 50.0) < 1e-12
    assert abs(q2 - (5000.0 / 105.9)) < 1e-12

    # Engine already charged 0.04%/side; settle with the default commission must keep those fees.
    net0, fee0 = settle(raw[0], ENGINE_FEE, 0.0)
    net1, fee1 = settle(raw[1], ENGINE_FEE, 0.0)
    expected_fee1 = ENGINE_FEE * q1 * (raw[0]["entry"] + raw[0]["exit"])
    expected_fee2 = ENGINE_FEE * q2 * (raw[1]["entry"] + raw[1]["exit"])
    assert abs(fee0 - expected_fee1) < 1e-12
    assert abs(fee1 - expected_fee2) < 1e-12
    # each fee is charged once, on that tranche's actual qty only
    assert abs(fee0 - (2.0 + 2.181)) < 1e-12          # 0.0004*50*100 + 0.0004*50*109.05
    assert abs(fee1 - (2.0 + 1.886685552407932)) < 1e-9

    report = _report_rows(engine)
    exits = [t["exit_reason"] for t in report["trades"]]
    assert exits == ["TP1", "Stop"]
    assert report["stats"]["total_trades"] == 2
    assert report["stats"]["exit_reasons"] == {"TP1": 1, "Stop": 1}

    # App export.csv columns
    from app import _csv
    csv_text = _csv(report)
    out = tmp_path / "turtle_tp1_stop_export.csv"
    out.write_text(csv_text)
    # Also materialize next to the tests for the review package
    dest = ROOT / "tests" / "turtle_tp1_stop_report_rows.txt"
    lines = ["# tester report rows (run.js turtle() + settle + _book_from_engine + run_backtest trade list)",
             f"# engine fees: TP1 {fee0:.12f}  Stop {fee1:.12f}",
             ""]
    for t in report["trades"]:
        lines.append(
            f"n={t['n']}  {t['side']}  {t['reason']}  "
            f"entry={t['entry_time']} @ {t['entry_price']}  "
            f"exit={t['exit_time']} @ {t['exit_price']}  "
            f"qty={t['qty']}  fee={t['commission']}  pnl={t['pnl']}"
        )
    lines.append("")
    lines.append(csv_text)
    dest.write_text("\n".join(lines) + "\n")
    assert "TP1" in csv_text and "Stop" in csv_text
    assert csv_text.count("\n") >= 3  # header + 2 rows
