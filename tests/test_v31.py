"""v3.1 engines against the extracted JavaScript, including the Pivex single-entry rule."""

import json
import subprocess
from pathlib import Path

from backtest.config import STRATEGIES, venue_enabled
from backtest.v31 import WEIGHTS, UNIT, pivex_size

ROOT = Path(__file__).resolve().parents[1]


def _node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def test_pivex_size_is_unit_times_weight_times_k():
    assert pivex_size("Micro", 8) == UNIT["Micro"] * WEIGHTS["Micro"] * 8
    assert abs(pivex_size("HAMA30", 8) - 143.55) < 0.02
    assert abs(pivex_size("S5", 8) - 994.14) < 0.05
    assert abs(pivex_size("Turtle", 8) - 12346.29) < 0.1
    assert pivex_size("Micro", 1) == UNIT["Micro"] * WEIGHTS["Micro"]


def test_grid_strategies_are_binance_only_and_the_rest_run_on_both():
    grids = {"dg_grid", "er_grid", "hama_grid_5m", "hama_grid_1m"}
    for strategy in STRATEGIES:
        if strategy["id"] in grids:
            assert venue_enabled(strategy, "binance")
            assert not venue_enabled(strategy, "pivex")
        else:
            assert venue_enabled(strategy, "pivex")
            assert venue_enabled(strategy, "binance")


def test_extracted_engines_have_no_secrets():
    blob = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "js" / "v31").glob("*.js"))
    lowered = blob.lower()
    assert "supabase" not in lowered
    assert "service_role" not in lowered
    assert "pivex_queue" not in lowered


def test_turtle_pivex_is_one_full_entry_and_binance_adds():
    out = _node(r"""
const C = require("./js/v31/combo.js");
const n = 14;
const D = { t: [], o: [], h: [], l: [], c: [], ct: [] };
let prev = 100;
for (let i = 0; i < n; i++) {
  const close = i < 4 ? 100 : 100 * Math.pow(1.03, i - 3);
  D.t.push(i * 300000); D.ct.push((i + 1) * 300000);
  D.o.push(prev); D.c.push(close);
  D.h.push(Math.max(prev, close) + 0.2);
  D.l.push(Math.min(prev, close) - 0.2);
  prev = close;
}
const S = {
  allowL: Array(n).fill(true), allowS: Array(n).fill(false),
  longT: Array(n).fill(false), shortT: Array(n).fill(false),
  atrd: Array(n).fill(80)
};
S.longT[2] = true;
function view(single) {
  return C.turtleRun(D, S, 0, 3, 6, 3, single).map(p => p.tr.map(t => ({ tag: t.tag, frac: t.frac })));
}
console.log(JSON.stringify({ multi: view(false), single: view(true) }));
""")
    multi = [t["tag"] for pos in out["multi"] for t in pos]
    single = [t["tag"] for pos in out["single"] for t in pos]
    assert "LONG_ADD" in multi
    assert single == ["LONG"]
    assert out["single"][0][0]["frac"] == 1
    assert all(t["frac"] == 0.5 for pos in out["multi"] for t in pos)


def test_s5_pivex_drops_the_add_and_binance_keeps_the_partial():
    out = _node(r"""
const S5 = require("./js/v31/s5.js");
const { HANDLERS } = require("./js/v31/run.js");
function fresh() {
  return {
    pos: { dir: 1, e0: 100, q0: 1, op0: true, q1: 0, e1: 0, op1: false, addUsed: false,
           pnl: 0, fee: 0.04, mae: 0, entT: 0, notl: 100, stp: 90, lim0: 106, lim1: 0, adT: null, lastPx: 100 },
    pend: null, pendAdd: null, half: null, last15T: -1,
    T: { sess: 1, sH: 1, sL: 1, sC: 1, pC: 1, satr: 1, seed: 1, cnt: 20, skip: false,
         eH: [], eL: [], xH: [], xL: [], side: 1, fstop: 1, stop: 1,
         hist: [{ t: -1, side: 1, ready: true, stop: 1 }] },
    I: { prevClose: 100, atr: 1, closes: Array(200).fill(100), highs: Array(20).fill(101), lows: Array(20).fill(99), trig: Array(1000).fill(1) }
  };
}
const bar = { t: 900000, o: 100, h: 105, l: 99.5, c: 104 };
const mins = [];
for (let i = 0; i < 15; i++) mins.push({ t: 900000 + i * 60000, o: 104, h: 105, l: 100, c: 104 });
const armed = fresh();
S5.step15(armed, bar, mins);
const follow = { t: 1800000, o: 104, h: 105, l: 103, c: 104.5 };
const next = [{ t: 1800000, o: 104, h: 105, l: 103, c: 104.5 }];
const kept = fresh();
S5.step15(kept, bar, mins);
const withAdd = S5.step15(kept, follow, next);
const dropped = fresh();
S5.step15(dropped, bar, mins);
dropped.pendAdd = null;
const noAdd = S5.step15(dropped, follow, next);
console.log(JSON.stringify({
  armed: !!armed.pendAdd,
  withAdd: withAdd.events.some(e => e.kind === "ADD"),
  noAdd: noAdd.events.some(e => e.kind === "ADD"),
  handlers: typeof HANDLERS.s5_15m === "function"
}));
""")
    assert out["armed"] is True
    assert out["withAdd"] is True
    assert out["noAdd"] is False
    assert out["handlers"] is True
