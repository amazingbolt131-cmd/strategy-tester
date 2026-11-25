// Deterministic fixtures for the v3.1 fill engines. Run: node --test js/research/fixtures.test.js
// Each test documents CURRENT behaviour (assert what the code does) and, where OHLC cannot
// reveal intrabar order, states the conservative alternative in the test name.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const V = path.join(__dirname, "..", "v31");
const COMBO = require(path.join(V, "combo.js"));
const S5 = require(path.join(V, "s5.js"));
const HAMA = require(path.join(V, "hama.js"));
const HG = require(path.join(V, "hg.js"));

const M5 = 300000, T0 = Date.UTC(2026, 0, 5, 0, 0);
function D5(ohlc) { // [[o,h,l,c],...] -> column bars
  const D = { t: [], o: [], h: [], l: [], c: [], ct: [] };
  ohlc.forEach(function (b, i) { D.t.push(T0 + i * M5); D.o.push(b[0]); D.h.push(b[1]); D.l.push(b[2]); D.c.push(b[3]); D.ct.push(T0 + (i + 1) * M5); });
  return D;
}
function S(n, sigIdx, side, atrd) {
  const z = function (v) { return new Array(n).fill(v); };
  const s = { longT: z(false), shortT: z(false), allowL: z(side === 1), allowS: z(side === -1), atrd: z(atrd) };
  (Array.isArray(sigIdx) ? sigIdx : [sigIdx]).forEach(function (k) { if (side === 1) s.longT[k] = true; else s.shortT[k] = true; });
  return s;
}
const flat = function (n, p) { return new Array(n).fill(0).map(function () { return [p, p, p, p]; }); };

// ---------- 1. entry after signal confirmation ----------
test("turtle: signal on bar k close fills at bar k+1 open, never at the signal close", function () {
  const bars = flat(10, 100); bars[3] = [100, 100, 99, 99.5]; bars[4] = [101, 101, 101, 101];
  const pos = COMBO.turtleRun(D5(bars), S(10, 3, 1, 50), 0, 3, 6, 3, true);
  assert.equal(pos[0].tr[0].sb, 3); assert.equal(pos[0].tr[0].eb, 4); assert.equal(pos[0].tr[0].ep, 101);
});
test("s5: pending entry fills at the first 1m open of the next 15m bar", function () {
  const st = { pos: null, pend: { dir: 1, q: 1 / 10, ad: 10 }, pendAdd: null, half: null, last15T: -1,
    T: { sess: null, sH: null, sL: null, sC: null, pC: null, satr: null, seed: 0, cnt: 0, skip: true, eH: [], eL: [], xH: [], xL: [], side: 0, fstop: null, stop: null, hist: [] },
    I: { prevClose: null, atr: null, closes: [], highs: [], lows: [], trig: [] } };
  const t = T0; const mins = [{ t: t, o: 200, h: 201, l: 199.5, c: 200.5 }, { t: t + 60000, o: 200.5, h: 201, l: 200, c: 200.8 }];
  const r = S5.step15(st, { t: t, o: 200, h: 201, l: 199.5, c: 200.8 }, mins);
  const e = r.events.find(function (x) { return x.kind === "ENTRY"; });
  assert.equal(e.t, t); assert.equal(e.price, 200);
});
test("hg: grid armed on the signal-bar close; level fills only on a later minute, at min(level, open) when the limit is touched", function () {
  const inc = HG.newInc(HG.CFG.g1m);
  inc.grid = { vd: 1, p: 100, qty: 0, cost: 0, nu: 0, lvl: 1, first: null, fees: 0, t0: T0 };
  inc.last_t = T0;
  HG.onMin(inc, T0 + 60000, 100, 100.1, 99.8, 99.9);      // L1 = 99.7 not touched
  assert.equal(inc.grid.nu, 0);
  HG.onMin(inc, T0 + 120000, 99.9, 99.9, 99.65, 99.8);    // touched -> fill at 99.7 (touch == fill: optimistic, no queue model)
  assert.equal(inc.grid.nu, 1); assert.ok(Math.abs(inc.grid.cost - 99.7) < 1e-9);
  HG.onMin(inc, T0 + 180000, 99.0, 99.1, 98.9, 99.0);     // gapped below L2 = 99.4 -> fill at the open 99.0
  assert.equal(inc.grid.nu, 2); assert.ok(Math.abs(inc.grid.cost - 99.7 - 99.0) < 1e-9);
});
test("hg: a minute that crashes through several levels fills only ONE level (one fill per minute)", function () {
  const inc = HG.newInc(HG.CFG.g1m);
  inc.grid = { vd: 1, p: 100, qty: 0, cost: 0, nu: 0, lvl: 1, first: null, fees: 0, t0: T0 }; inc.last_t = T0;
  HG.onMin(inc, T0 + 60000, 100, 100, 98.95, 99.0); // L1..L3 (99.7, 99.4, 99.1) all inside the bar
  assert.equal(inc.grid.nu, 1);
});

// ---------- 2. stop and target touched on the same bar ----------
test("turtle/ML bar path: (h-o)<=(o-l) walks o->h->l->c, so a long whose TP and stop are both inside the bar takes the TP (assumed path, not verified)", function () {
  const bars = flat(8, 100); bars[2] = [100, 100, 100, 100]; bars[3] = [100, 107, 90, 95]; // h-o = 7 <= o-l = 10 -> o,h,l,c
  const pos = COMBO.turtleRun(D5(bars), S(8, 1, 1, 5), 0, 3, 6, 3, true);
  assert.equal(pos[0].tr[0].why, "TP1"); assert.equal(pos[0].tr[0].xb, 3);
  // conservative rule (stop first) would book a Stop at 95 on bar 3.
});
test("s5: stop is checked before targets inside one 1m bar (conservative)", function () {
  const st = { pos: { dir: 1, e0: 100, q0: 1, op0: true, q1: 0, e1: 0, op1: false, addUsed: false, pnl: 0, fee: 0.04, mae: 0, entT: T0, notl: 100, stp: 95, lim0: 106, lim1: 0, adT: null },
    pend: null, pendAdd: null, half: null, last15T: -1,
    T: { sess: null, sH: null, sL: null, sC: null, pC: null, satr: null, seed: 0, cnt: 0, skip: true, eH: [], eL: [], xH: [], xL: [], side: 0, fstop: null, stop: null, hist: [] },
    I: { prevClose: null, atr: null, closes: [], highs: [], lows: [], trig: [] } };
  const r = S5.step15(st, { t: T0, o: 100, h: 107, l: 94, c: 100 }, [{ t: T0, o: 100, h: 107, l: 94, c: 100 }]);
  const x = r.events.find(function (e) { return e.kind === "EXIT"; });
  assert.equal(x.why, "STOP"); assert.equal(x.price, 95);
});
test("micro: stop first when stop and target share a 5m bar (conservative)", function () {
  const b5 = { t: [T0, T0 + M5], h: [101, 120], l: [99, 80] };
  const x = COMBO.microExit(b5, T0 + M5, 1, 90, 110);
  assert.equal(x.why, "SL");
});
test("hg: stop has priority over target in the same minute (conservative)", function () {
  const inc = HG.newInc(HG.CFG.g1m);
  inc.grid = { vd: 1, p: 100, qty: 1, cost: 99.7, nu: 1, lvl: 2, first: T0, fees: 0.0004, t0: T0 }; inc.last_t = T0;
  HG.onMin(inc, T0 + 60000, 100, 107, 97, 100); // stop 98.2, tp 106 both inside
  assert.equal(inc.trades[0].why, 1);
});

// ---------- 3. add and stop on the same bar ----------
test("turtle (Binance, two tranches): when the add and a stop share a bar, the basket stop closes BOTH open tranches on that bar", function () {
  // Previously documented the unpatched bug (only tranche 1 stopped; add closed late on the next bar).
  const bars = flat(12, 100);
  bars[3] = [100, 104, 100, 104]; // +4% close -> add armed on bar 3
  bars[4] = [104, 104, 90, 92];   // add fills at 104 open, then price falls through the basket stop (avg 101.96 - 6 = 95.96)
  const pos = COMBO.turtleRun(D5(bars), S(12, 1, 1, 6), 0, 3, 6, 3, false);
  const P = pos[0];
  assert.equal(P.tr[1].eb, 4); assert.equal(P.tr[1].ep, 104);
  assert.equal(P.tr.length, 2);
  for (const t of P.tr) { assert.equal(t.why, "Stop"); assert.equal(t.xb, 4); assert.ok(Math.abs(t.xp - 95.9608) < 1e-3); }
  assert.ok(!P.open);
});

// ---------- 4. partial exit followed by a stop ----------
test("turtle (Binance): TP1 closes only the first tranche; the add tranche is later stopped; both legs are ONE basket", function () {
  const bars = flat(14, 100);
  bars[3] = [100, 103.5, 100, 103.5];   // add armed (+3.5%)
  bars[4] = [103.5, 103.6, 103.4, 103.5];// add fills at 103.5 (avg ~101.72); TP1 = avg*1.06 ~ 107.8; TP2 = avg*1.03 ~ 104.8
  bars[5] = [103.5, 104.0, 103.0, 103.8];
  bars[6] = [103.8, 108.5, 103.7, 108];  // h-o 4.7 > o-l 0.1 -> path o,l,h,c: TP1 + TP2 both hit -> flat
  const pos = COMBO.turtleRun(D5(bars), S(14, 1, 1, 6), 0, 3, 6, 3, false);
  assert.equal(pos[0].tr.length, 2);
  assert.deepEqual(pos[0].tr.map(function (t) { return t.why; }).sort(), ["TP1", "TP2"]);
  // run.js emits these as two rows -> the report counts 2 "trades" for 1 basket.
});
test("s5: TP1 closes tranche 1 only, a later stop closes tranche 2 at the (dynamic) stop", function () {
  const st = { pos: { dir: 1, e0: 100, q0: 1, op0: true, q1: 1, e1: 103, op1: true, addUsed: true, pnl: 0, fee: 0, mae: 0, entT: T0, notl: 203, stp: 97, lim0: 107.5, lim1: 110, adT: T0 },
    pend: null, pendAdd: null, half: null, last15T: -1,
    T: { sess: null, sH: null, sL: null, sC: null, pC: null, satr: null, seed: 0, cnt: 0, skip: true, eH: [], eL: [], xH: [], xL: [], side: 0, fstop: null, stop: null, hist: [] },
    I: { prevClose: null, atr: null, closes: [], highs: [], lows: [], trig: [] } };
  const r = S5.step15(st, { t: T0, o: 107, h: 108, l: 96, c: 96 }, [{ t: T0, o: 107, h: 108, l: 106.9, c: 107.5 }, { t: T0 + 60000, o: 107.5, h: 107.5, l: 96, c: 96 }]);
  const k = r.events.map(function (e) { return e.kind + (e.tranche || "") + (e.why || ""); });
  assert.deepEqual(k.slice(0, 2), ["TP1", "EXITSTOP"]);
});

// ---------- 5. gap through stop ----------
test("HAMA: a bar that OPENS beyond the stop is filled AT THE STOP, not at the open (optimistic on gaps)", function () {
  const bars = []; for (let i = 0; i < 4; i++) bars.push({ t: T0 + i * 1800000, ct: T0 + (i + 1) * 1800000, o: 100, h: 100.5, l: 99.5, c: 100 });
  bars[2] = { t: T0 + 2 * 1800000, ct: T0 + 3 * 1800000, o: 95, h: 96, l: 94, c: 95 }; // stop 99 -> opens at 95
  const r = HAMA.run({ gap: 0.3, R: 100, steps: 1 }, { chart: bars, htf: null, h1: bars },
    { initialPos: { side: 1, entry: 100, stop: 99, units: 100, risk: 100, entryMs: T0 } });
  assert.equal(r.trades[0].why, "STOP"); assert.equal(r.trades[0].exit, 99); // a conservative fill is the open, 95
});
test("micro: gap below the stop on the 5m tape is filled at the stop price (optimistic on gaps)", function () {
  const b5 = { t: [T0], h: [85], l: [80], o: [84] };
  assert.equal(COMBO.microExit(b5, T0, 1, 90, 110).xp, 90);
});
test("turtle/ML walk: a bar opening beyond the stop fills at the open (gap-aware)", function () {
  const bars = flat(8, 100); bars[3] = [90, 91, 89, 90];
  const pos = COMBO.turtleRun(D5(bars), S(8, 1, 1, 5), 0, 3, 6, 3, true);
  assert.equal(pos[0].tr[0].why, "Stop"); assert.equal(pos[0].tr[0].xp, 90);
});
test("s5: gap at the 1m open beyond the stop fills at the open", function () {
  const st = { pos: { dir: 1, e0: 100, q0: 1, op0: true, q1: 0, e1: 0, op1: false, addUsed: false, pnl: 0, fee: 0, mae: 0, entT: T0, notl: 100, stp: 95, lim0: 106, lim1: 0, adT: null },
    pend: null, pendAdd: null, half: null, last15T: -1,
    T: { sess: null, sH: null, sL: null, sC: null, pC: null, satr: null, seed: 0, cnt: 0, skip: true, eH: [], eL: [], xH: [], xL: [], side: 0, fstop: null, stop: null, hist: [] },
    I: { prevClose: null, atr: null, closes: [], highs: [], lows: [], trig: [] } };
  const r = S5.step15(st, { t: T0, o: 90, h: 91, l: 89, c: 90 }, [{ t: T0, o: 90, h: 91, l: 89, c: 90 }]);
  assert.equal(r.events.find(function (e) { return e.kind === "EXIT"; }).price, 90);
});

// ---------- 6. open basket at cutoff ----------
test("turtle: a basket still open at the end of data is flagged open; run.js then drops its open tranches (no END mark)", function () {
  const pos = COMBO.turtleRun(D5(flat(8, 100)), S(8, 1, 1, 50), 0, 3, 6, 3, true);
  assert.equal(pos.length, 1); assert.equal(pos[0].open, true); assert.equal(pos[0].tr[0].xb, undefined);
});

// ---------- 7. higher-timeframe rollover ----------
test("NY 17:00 session key flips exactly at 17:00 New York in both EDT and EST", function () {
  // EDT (UTC-4): 2026-10-30 17:00 NY = 21:00 UTC ; EST (UTC-5): 2026-11-02 17:00 NY = 22:00 UTC
  [[Date.UTC(2026, 9, 30, 21, 0)], [Date.UTC(2026, 10, 2, 22, 0)], [Date.UTC(2026, 2, 9, 21, 0)]].forEach(function (x) {
    assert.equal(S5.sessKey(x[0]) - S5.sessKey(x[0] - 60000), 1);
    assert.equal(S5.sessKey(x[0] + 60000) - S5.sessKey(x[0]), 0);
  });
  // DST change day 2026-11-01: 21:00 UTC is 16:00 EST (still the old session), 22:00 UTC is 17:00 EST (new)
  const a = Date.UTC(2026, 10, 1, 21, 0), b = Date.UTC(2026, 10, 1, 22, 0);
  assert.equal(S5.sessKey(b) - S5.sessKey(a), 1);
});
test("s5 biasFor: a 15m bar uses only a 30m bar that closed at or before the 15m bar's open bucket", function () {
  const T = { hist: [{ t: T0, side: 1, ready: true }, { t: T0 + 1800000, side: -1, ready: true }] };
  assert.equal(S5.biasFor(T, T0 + 1800000).side, 1);           // 15m bar 00:30-00:45 -> uses 00:00-00:30 bar
  assert.equal(S5.biasFor(T, T0 + 1800000 + 900000).side, 1);  // 15m bar 00:45-01:00 -> still 00:00-00:30
  assert.equal(S5.biasFor(T, T0 + 3600000).side, -1);          // 01:00 bar -> 00:30-01:00 bar
});
test("turtle 30m bias: a 5m bar uses the 30m bucket that ENDS at or before the start of its own bucket", function () {
  // 30m bias for 5m bar at t uses bucket (t - t%30m) - 30m, i.e. up to 30 min stale, never the forming bucket.
  const n = 12 * 6; const D = D5(flat(n, 100));
  const b = COMBO.turtleBias30m(D);
  assert.equal(b.side.length, n); assert.equal(b.ready.every(function (x) { return x === false; }), true); // not enough sessions -> never ready
});
