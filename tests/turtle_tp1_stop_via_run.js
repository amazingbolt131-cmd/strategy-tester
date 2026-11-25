/* Drive the TP1-then-stop fixture through the same reporting loop as js/v31/run.js turtle().
   Signals are injected (16 synthetic bars cannot satisfy turtleSignals), but qty, sizeNorm,
   0.04%/side fee, scaleOf, and the per-tranche trade rows are the production ones. */
"use strict";
const path = require("path");
const COMBO = require(path.join(__dirname, "..", "js", "v31", "combo.js"));
const FEE = 0.0004;
const M5 = 300000, T0 = Date.UTC(2026, 0, 5, 0, 0);

function barsFixture() {
  const ohlc = Array.from({ length: 16 }, () => [100, 100, 100, 100]);
  ohlc[2] = [100, 100.2, 99.8, 100];
  ohlc[3] = [100, 103.2, 99.9, 103.2];
  ohlc[4] = [105.9, 105.9, 105.8, 105.85];
  ohlc[6] = [109.05, 109.06, 108.9, 108.95];
  ohlc[8] = [108.0, 108.0, 99.0, 100.0];
  return ohlc.map((b, i) => ({ t: T0 + i * M5, o: b[0], h: b[1], l: b[2], c: b[3], ct: T0 + (i + 1) * M5 }));
}
function S(n) {
  const z = (v) => Array(n).fill(v);
  const s = { longT: z(false), shortT: z(false), allowL: z(true), allowS: z(false), atrd: z(6), bias: z(1) };
  s.longT[1] = true;
  return s;
}
function scaleOf(venue, k) {
  if (venue !== "pivex") return 1;
  return COMBO.WEIGHTS.Turtle * k;
}
function row(t) {
  return {
    side: t.side, qty: t.qty, entry_ms: t.entry_ms, exit_ms: t.exit_ms,
    entry: t.entry, exit: t.exit, stop: null, tp: null,
    entry_reason: t.entry_reason, exit_reason: t.exit_reason,
    gross: t.gross, fee: t.fee, net: t.net,
    run_up: 0, drawdown: 0, mult: 1, tranche: t.tranche, adjustable: true
  };
}

const venue = process.env.VENUE || "binance";
const k = +(process.env.K || 8);
const rows5 = barsFixture();
const D = COMBO.barsOf(rows5);
const raw = COMBO.turtleRun(D, S(rows5.length), 0, 3, 6, 3, venue === "pivex");
const scale = scaleOf(venue, k);
const sp0 = COMBO.sizeNorm(raw, D, 0);
const spf = COMBO.sizeNorm(raw, D, FEE);
const trades = [];
for (let i = 0; i < sp0.length; i++) {
  const P0 = sp0[i], Pf = spf[i];
  for (let j = 0; j < P0.tr.length; j++) {
    const t = P0.tr[j];
    if (t.xb == null) continue;
    const exitMs = D.t[t.xb] + 300000;
    const entryMs = D.t[t.eb];
    const gross = t.pnl * scale;
    const net = Pf.tr[j].pnl * scale;
    trades.push(row({
      side: t.side, qty: t.q * scale, entry_ms: entryMs, exit_ms: exitMs,
      entry: t.ep, exit: t.xp,
      entry_reason: t.tag, exit_reason: t.why,
      gross: gross, fee: gross - net, net: net,
      tranche: t.tag.indexOf("ADD") >= 0 ? 2 : 1
    }));
  }
}
process.stdout.write(JSON.stringify({
  trades: trades,
  meta: { scale: scale, note: "Binance multi-entry. Full book $10,000. First fill is half, the +3% add is the other half. TP1 closes only the first half.",
          n_baskets: raw.length, n_tranches: raw[0] ? raw[0].tr.length : 0, start: T0, end: T0 + 16 * M5 }
}));
