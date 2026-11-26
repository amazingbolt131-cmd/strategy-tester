/* HAMA 1h + Reversal Turtle MTF 5m combo: entry signals only.
   Turtle entries come straight from the official combo.js engine (turtleSignals:
   5m trigger, 30m session bias, atrd stop distance). The HAMA 1h trend state uses
   the same HAMA Stable maths as hama.js (CO 25 / CC 20, ATR 14, gap 0.3 x ATR,
   4h HTF colour, 1h EMA200 side gate). hama.js only exports run() and emaTv(), so
   the trend helpers below are copied verbatim from it; hama.js and combo.js are
   not modified. A Turtle signal is kept only when the last completed 1h HAMA bar
   (close time <= the 5m signal close, so no lookahead) is ready and on the same
   side. Exits (official stop, half off at +3% then BE, rest to 6%) are walked on
   1m bars in backtest/combo_ht.py.
   Input (stdin JSON): {m5, m1h, m4h}: rows of [open_ms, o, h, l, c].
   Output: {t: [...5m open ms], side: [1|-1|0], atrd: [...], raw: [...]}. */
"use strict";

const fs = require("fs");
const COMBO = require("./combo.js");
const HAMA = require("./hama.js");

// ----- copied from hama.js (internal helpers it does not export) -----
var CO_LEN = 25, CC_LEN = 20, ATR_LEN = 14, EMA200_LEN = 200;
var emaTv = HAMA.emaTv;

function atrTv(h, l, c, len) {
  var n = c.length, tr = new Array(n);
  tr[0] = h[0] - l[0];
  for (var i = 1; i < n; i++) {
    var hl = h[i] - l[i], hc = Math.abs(h[i] - c[i - 1]), lc = Math.abs(l[i] - c[i - 1]);
    var m = hl > hc ? hl : hc; if (lc > m) m = lc; tr[i] = m;
  }
  return emaTv(tr, len, true);
}
function hamaLines(o, h, l, c) {
  var n = c.length, mid = new Array(n), o4 = new Array(n);
  mid[0] = NaN;
  for (var i = 1; i < n; i++) mid[i] = 0.5 * (o[i - 1] + c[i - 1]);
  for (var j = 0; j < n; j++) o4[j] = (o[j] + h[j] + l[j] + c[j]) * 0.25;
  return { co: emaTv(mid, CO_LEN, false), cc: emaTv(o4, CC_LEN, false) };
}
function asofIdx(srcCt, x, strict) {
  var lo = 0, hi = srcCt.length;
  while (lo < hi) { var m = (lo + hi) >> 1; if (strict ? srcCt[m] < x : srcCt[m] <= x) lo = m + 1; else hi = m; }
  return lo - 1;
}
function htfGreen(htf, chartCt) {
  var L = hamaLines(htf.o, htf.h, htf.l, htf.c);
  var g = L.cc.map(function (v, i) { return (isNaN(v) || isNaN(L.co[i])) ? NaN : (v >= L.co[i] ? 1 : 0); });
  return chartCt.map(function (t) { var k = asofIdx(htf.ct, t, true); return k >= 0 ? g[k] : NaN; });
}
function emaSide(h1, chartCt) {
  var e = emaTv(h1.c, EMA200_LEN, false);
  return chartCt.map(function (t) {
    var k = asofIdx(h1.ct, t, false);
    if (k < 0 || isNaN(h1.c[k]) || isNaN(e[k])) return 0;
    return h1.c[k] > e[k] ? 1 : (h1.c[k] < e[k] ? -1 : 0);
  });
}
function stableState(cc, co, atr, hg, gap, useHtf) {
  var n = cc.length, state = new Array(n).fill(0), started = false, prev = 0;
  for (var i = 0; i < n; i++) {
    if (isNaN(cc[i]) || isNaN(co[i]) || isNaN(atr[i])) { state[i] = prev; continue; }
    if (useHtf && isNaN(hg[i])) { state[i] = prev; continue; }
    var wantG = cc[i] >= co[i], wantR = !wantG;
    var gapOk = gap > 0 ? Math.abs(cc[i] - co[i]) >= gap * atr[i] : true;
    var htfG = true, htfR = true;
    if (useHtf) { htfG = hg[i] >= 0.5; htfR = !htfG; }
    var condG = wantG && gapOk && htfG, condR = wantR && gapOk && htfR;
    if (!started) { prev = wantG ? 1 : -1; state[i] = prev; started = true; continue; }
    var cur = prev;
    if (prev === -1 && condG) cur = 1; else if (prev === 1 && condR) cur = -1;
    state[i] = cur; prev = cur;
  }
  return state;
}
// ----- end of copied helpers -----

function hamaTrend(chart, htf, h1, gap) {
  var L = hamaLines(chart.o, chart.h, chart.l, chart.c);
  var atr = atrTv(chart.h, chart.l, chart.c, ATR_LEN);
  var hg = htfGreen(htf, chart.ct);
  var state = stableState(L.cc, L.co, atr, hg, gap, true);
  var es = emaSide(h1, chart.ct);
  var ready = new Array(state.length);
  for (var i = 0; i < state.length; i++) {
    ready[i] = (state[i] !== 0 && es[i] === state[i] && !isNaN(L.cc[i]) && !isNaN(L.co[i]) && !isNaN(atr[i])) ? 1 : 0;
  }
  return { ct: chart.ct, state: state, ready: ready };
}

function cols(rows, ms) {
  var t = [], o = [], h = [], l = [], c = [], ct = [];
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    t.push(r[0]); o.push(r[1]); h.push(r[2]); l.push(r[3]); c.push(r[4]); ct.push(r[0] + ms);
  }
  return { t: t, o: o, h: h, l: l, c: c, ct: ct };
}

function signals(req) {
  var D = COMBO.barsOf(cols(req.m5, 300000));
  var S = COMBO.turtleSignals(D);
  var h1 = cols(req.m1h, 3600000);
  var h4 = cols(req.m4h, 14400000);
  var trend = hamaTrend(h1, h4, h1, 0.3);
  var n = D.t.length, side = new Array(n), raw = new Array(n), atrd = new Array(n);
  for (var i = 0; i < n; i++) {
    var s = 0;
    if (S.allowL[i] && S.longT[i]) s = 1;
    else if (S.allowS[i] && S.shortT[i]) s = -1;
    raw[i] = s;
    var k = asofIdx(trend.ct, D.ct[i], false);
    var ok = k >= 0 && trend.ready[k] === 1 && trend.state[k] === s;
    side[i] = s !== 0 && ok ? s : 0;
    atrd[i] = (typeof S.atrd[i] === "number" && isFinite(S.atrd[i])) ? S.atrd[i] : null;
  }
  return { t: D.t, side: side, raw: raw, atrd: atrd };
}

if (require.main === module) {
  try {
    var req = JSON.parse(fs.readFileSync(0, "utf8"));
    process.stdout.write(JSON.stringify(signals(req)));
  } catch (err) {
    process.stderr.write(String(err && err.stack || err) + "\n");
    process.exit(1);
  }
} else {
  module.exports = { signals: signals };
}
