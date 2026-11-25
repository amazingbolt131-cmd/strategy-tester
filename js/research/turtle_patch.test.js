// Regression fixture for research_out/v2/turtle_patch/turtle_basket_stop.patch.
// Run against the patched engine:   COMBO_JS=/path/to/patched/js/v31/combo.js node --test js/research/turtle_patch.test.js  (must PASS)
// Run against unpatched 7a48bb2:    node --test js/research/turtle_patch.test.js                                            (must FAIL test 1)
"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), path = require("path");
const COMBO = require(process.env.COMBO_JS || path.join(__dirname, "..", "v31", "combo.js"));
const M5 = 300000, T0 = Date.UTC(2026, 0, 5, 0, 0);
function D5(ohlc) { const t = ohlc.map((_, i) => T0 + i * M5); return { t, o: ohlc.map(x => x[0]), h: ohlc.map(x => x[1]), l: ohlc.map(x => x[2]), c: ohlc.map(x => x[3]), ct: t.map(x => x + M5) }; }
function flat(n, p) { return Array.from({ length: n }, () => [p, p, p, p]); }
function S(n, side, atrd) { const a = Array(n).fill(false); const L = a.slice(), Sg = a.slice(); L[1] = side === 1; Sg[1] = side === -1;
  return { longT: L, shortT: Sg, allowL: Array(n).fill(side === 1), allowS: Array(n).fill(side === -1), atrd: Array(n).fill(atrd), bias: Array(n).fill(side) }; }
test("turtle basket stop: add + stop on the same bar closes BOTH tranches at the basket stop on that bar", function () {
  const bars = flat(12, 100);
  bars[3] = [100, 104, 100, 104];   // +4% close arms the add
  bars[4] = [104, 104, 90, 92];     // add fills at 104, price falls through the stop avg(100,104)-6 = 95.9608
  const P = COMBO.turtleRun(D5(bars), S(12, 1, 6), 0, 3, 6, 3, false)[0];
  assert.equal(P.tr.length, 2);
  for (const t of P.tr) { assert.equal(t.why, "Stop"); assert.equal(t.xb, 4); assert.ok(Math.abs(t.xp - 95.9608) < 1e-3); }
  assert.ok(!P.open);
});
test("turtle basket stop: TP1 still closes only tranche 1 (partial exit unchanged)", function () {
  const bars = flat(14, 100);
  bars[3] = [100, 103.5, 100, 103.5]; bars[4] = [103.5, 103.6, 103.4, 103.5]; bars[5] = [103.5, 104.0, 103.0, 103.8]; bars[6] = [103.8, 108.5, 103.7, 108];
  const P = COMBO.turtleRun(D5(bars), S(14, 1, 6), 0, 3, 6, 3, false)[0];
  assert.deepEqual(P.tr.map(t => t.why).sort(), ["TP1", "TP2"]);
});
test("turtle basket stop: single-entry (Pivex) path is unaffected", function () {
  const bars = flat(10, 100); bars[4] = [100, 100, 90, 92];
  const P = COMBO.turtleRun(D5(bars), S(10, 1, 6), 0, 3, 6, 3, true)[0];
  assert.equal(P.tr.length, 1); assert.equal(P.tr[0].why, "Stop"); assert.equal(P.tr[0].xp, 94);
});

// Fixture requested for review: two-tranche basket, TP1 already closed tranche 1, then the stop
// must close ONLY the remaining add, with one exit and one exit-fee on remaining qty. Both the
// original and patched engines are expected to PASS — this guards against a duplicate close/fee.
test("turtle basket stop: after TP1 has closed tranche 1, a later stop closes only the remaining tranche (one exit, one fee, no duplicate)", function () {
  const bars = flat(16, 100);
  bars[2] = [100, 100.2, 99.8, 100];
  bars[3] = [100, 103.2, 99.9, 103.2];     // close >= +3% arms the add
  bars[4] = [105.9, 105.9, 105.8, 105.85]; // add fills at 105.9 (below combined TP2 105.9514)
  bars[6] = [109.05, 109.06, 108.9, 108.95]; // gap open in [combined TP1 109.0374, post-TP1 TP2 109.077)
  bars[8] = [108.0, 108.0, 99.0, 100.0];   // walks down through remaining stop 105.9 - 6 = 99.9
  const P = COMBO.turtleRun(D5(bars), S(16, 1, 6), 0, 3, 6, 3, false)[0];
  assert.ok(P && !P.open);
  assert.equal(P.tr.length, 2);

  const t1 = P.tr[0], t2 = P.tr[1];
  assert.equal(t1.tag, "LONG");
  assert.equal(t1.why, "TP1");
  assert.equal(t1.xb, 6);
  assert.equal(t1.xp, 109.05);
  assert.equal(t2.tag, "LONG_ADD");
  assert.equal(t2.why, "Stop");
  assert.equal(t2.xb, 8);
  assert.ok(Math.abs(t2.xp - 99.9) < 1e-12);

  // TP1 tranche must not be rewritten when the later stop fills
  assert.notEqual(t1.xb, t2.xb);
  assert.equal(P.tr.filter(t => t.why === "TP1").length, 1);
  assert.equal(P.tr.filter(t => t.why === "Stop").length, 1);
  assert.equal(P.tr.filter(t => t.xb === 8).length, 1);

  const FEE = 0.0004;
  const q1 = 10000 * t1.frac / t1.ep; // 50
  const q2 = 10000 * t2.frac / t2.ep; // 10000 * 0.5 / 105.9
  const feeExit1 = FEE * q1 * t1.xp;
  const feeExit2 = FEE * q2 * t2.xp;
  assert.ok(Math.abs(q1 - 50) < 1e-12);
  assert.ok(Math.abs(q2 - (5000 / 105.9)) < 1e-12);
  // exit fee on the stop uses remaining qty only; TP1 exit fee uses tranche-1 qty only
  assert.ok(Math.abs(feeExit1 - FEE * 50 * 109.05) < 1e-12);
  assert.ok(Math.abs(feeExit2 - FEE * (5000 / 105.9) * 99.9) < 1e-12);
  assert.ok(Math.abs(feeExit1 + feeExit2 - (FEE * (q1 * t1.xp + q2 * t2.xp))) < 1e-12);
});
