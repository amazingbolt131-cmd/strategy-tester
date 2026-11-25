// ===== HAMA Stable engine: port of hama_stable_lab/backtest.py + hama_multi_2y/run.py =====
var HAMA = (function () {
  var CO_LEN = 25, CC_LEN = 20, ATR_LEN = 14, EMA200_LEN = 200, SWING_LEN = 10;
  var ATR_STOP_MULT = 0.20, MIN_STOP = 0.25;

  // ta.ema: SMA seed of first `len` valid samples, then EMA; a NaN after seeding ends the series.
  function emaTv(src, len, wilder) {
    var n = src.length, out = new Array(n).fill(NaN), start = 0;
    while (start < n && isNaN(src[start])) start++;
    if (n - start < len) return out;
    var i = start;
    while (i + len <= n) {
      var ok = true, acc = 0;
      for (var k = i; k < i + len; k++) {
        var v = src[k];
        if (isNaN(v)) { ok = false; i = k + 1; break; }
        acc += v;
      }
      if (ok) {
        var seedEnd = i + len - 1;
        out[seedEnd] = acc / len;
        var a = wilder ? 1 / len : 2 / (len + 1), last = out[seedEnd];
        for (var k2 = seedEnd + 1; k2 < n; k2++) {
          var v2 = src[k2];
          if (isNaN(v2)) { out[k2] = NaN; return out; }
          last = a * v2 + (1 - a) * last;
          out[k2] = last;
        }
        return out;
      }
    }
    return out;
  }
  function atrTv(h, l, c, len) {
    var n = c.length, tr = new Array(n);
    tr[0] = h[0] - l[0];
    for (var i = 1; i < n; i++) {
      var hl = h[i] - l[i], hc = Math.abs(h[i] - c[i - 1]), lc = Math.abs(l[i] - c[i - 1]);
      var m = hl > hc ? hl : hc; if (lc > m) m = lc; tr[i] = m;
    }
    return emaTv(tr, len, true);
  }
  function swings(h, l, len) {
    var n = h.length, swh = new Array(n).fill(NaN), swl = new Array(n).fill(NaN);
    for (var i = len - 1; i < n; i++) {
      var mx = h[i - len + 1], mn = l[i - len + 1];
      for (var k = i - len + 2; k <= i; k++) { if (h[k] > mx) mx = h[k]; if (l[k] < mn) mn = l[k]; }
      swh[i] = mx; swl[i] = mn;
    }
    return { swh: swh, swl: swl };
  }
  function hamaLines(o, h, l, c) {
    var n = c.length, mid = new Array(n), o4 = new Array(n);
    mid[0] = NaN;
    for (var i = 1; i < n; i++) mid[i] = 0.5 * (o[i - 1] + c[i - 1]);
    for (var j = 0; j < n; j++) o4[j] = (o[j] + h[j] + l[j] + c[j]) * 0.25;
    return { co: emaTv(mid, CO_LEN, false), cc: emaTv(o4, CC_LEN, false) };
  }
  // bars: [{t: openMs, ct: closeMs (open + tf), o,h,l,c}]
  function cols(bars) {
    return {
      o: bars.map(function (b) { return b.o; }), h: bars.map(function (b) { return b.h; }),
      l: bars.map(function (b) { return b.l; }), c: bars.map(function (b) { return b.c; }),
      ct: bars.map(function (b) { return b.ct; })
    };
  }
  // last src index with src.ct < x (strict) or <= x
  function asofIdx(srcCt, x, strict) {
    var lo = 0, hi = srcCt.length;
    while (lo < hi) { var m = (lo + hi) >> 1; if (strict ? srcCt[m] < x : srcCt[m] <= x) lo = m + 1; else hi = m; }
    return lo - 1;
  }
  function htfGreen(htfBars, chartCt) {
    var X = cols(htfBars), L = hamaLines(X.o, X.h, X.l, X.c);
    var g = L.cc.map(function (v, i) { return (isNaN(v) || isNaN(L.co[i])) ? NaN : (v >= L.co[i] ? 1 : 0); });
    return chartCt.map(function (t) { var k = asofIdx(X.ct, t, true); return k >= 0 ? g[k] : NaN; });
  }
  function emaSide(h1Bars, chartCt) {
    var X = cols(h1Bars), e = emaTv(X.c, EMA200_LEN, false);
    return chartCt.map(function (t) {
      var k = asofIdx(X.ct, t, false);
      if (k < 0 || isNaN(X.c[k]) || isNaN(e[k])) return 0;
      return X.c[k] > e[k] ? 1 : (X.c[k] < e[k] ? -1 : 0);
    });
  }
  function stableState(cc, co, atr, hg, gap, useHtf) {
    var n = cc.length, state = new Array(n).fill(0), flip = new Array(n).fill(0), started = false, prev = 0;
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
      state[i] = cur; if (cur !== prev) flip[i] = 1; prev = cur;
    }
    return { state: state, flip: flip };
  }

  // cfg: {id, gap, R, steps}; data: {chart, htf (or null), h1}
  // opts: {startMs, endMs, forceCloseLast, minStopAt(ct, entry) -> $ min stop (multi-symbol: entry*0.25/SOL close; default $0.25)} (forceCloseLast only for backtest parity)
  function run(cfg, data, opts) {
    opts = opts || {};
    var X = cols(data.chart), n = X.c.length;
    var L = hamaLines(X.o, X.h, X.l, X.c), atr = atrTv(X.h, X.l, X.c, ATR_LEN), sw = swings(X.h, X.l, SWING_LEN);
    var useHtf = !!data.htf;
    var hg = useHtf ? htfGreen(data.htf, X.ct) : new Array(n).fill(0);
    var es = emaSide(data.h1, X.ct);
    var S = stableState(L.cc, L.co, atr, hg, cfg.gap, useHtf);
    var startMs = opts.startMs == null ? -Infinity : opts.startMs, endMs = opts.endMs == null ? Infinity : opts.endMs;
    var afterMs = opts.afterMs != null ? opts.afterMs : -Infinity;
    var lastI = -1;
    for (var q = n - 1; q >= 0; q--) if (X.ct[q] <= endMs) { lastI = q; break; }
    var step = cfg.R / cfg.steps, risk = opts.initialRisk != null ? opts.initialRisk : step;
    var pos = 0, entry = 0, stop = 0, units = 0, entryI = -1, entryT = 0, entryRisk = 0;
    if (opts.initialPos) {
      var ip = opts.initialPos;
      pos = ip.side; entry = ip.entry; stop = ip.stop; units = ip.units;
      entryRisk = ip.risk; entryT = ip.entryMs; entryI = -1;
    }
    var trades = [], events = [];
    for (var i = 0; i <= lastI; i++) {
      if (X.ct[i] <= afterMs) continue;
      if (pos !== 0 && i > entryI) {
        var hit = false, px = 0, why = '';
        if (pos === 1 && X.l[i] <= stop) { hit = true; px = stop; }
        else if (pos === -1 && X.h[i] >= stop) { hit = true; px = stop; }
        var opp = S.flip[i] !== 0 && S.state[i] === -pos, doExit = false;
        if (hit) { doExit = true; why = 'STOP'; }
        else if (opp) { doExit = true; px = X.c[i]; why = 'FLIP'; }
        else if (opts.forceCloseLast && i === lastI) { doExit = true; px = X.c[i]; why = 'END'; }
        if (doExit) {
          var pnl = units * (px - entry) * pos;
          var tr = { side: pos, entryMs: entryT, exitMs: X.ct[i], entry: entry, stop: stop, exit: px, units: units, risk: entryRisk, pnl: pnl, why: why };
          trades.push(tr);
          events.push({ t: X.ct[i], kind: 'EXIT', side: pos, price: px, why: why, pnl: pnl, risk: entryRisk, units: units, stop: stop });
          if (pnl < 0) risk = Math.min(cfg.R, risk + step); else if (pnl > 0) risk = step;
          pos = 0; units = 0;
        }
      }
      if (pos === 0 && S.flip[i] !== 0 && S.state[i] !== 0 && X.ct[i] >= startMs && (!opts.forceCloseLast || i < lastI)) {
        var side = S.state[i] === 1 ? 1 : -1, allow = true;
        if (side === 1 && es[i] !== 1) allow = false;
        if (side === -1 && es[i] !== -1) allow = false;
        // Daily HAMA colour gate. The live coordinator applies this at entry, not inside the
        // published run(); a rejected flip stays flat and does not move the loss step.
        if (allow && opts.allow && !opts.allow(X.ct[i], side)) allow = false;
        if (allow && !isNaN(atr[i]) && !isNaN(sw.swh[i]) && !isNaN(sw.swl[i])) {
          var e0 = X.c[i], md = Math.max(ATR_STOP_MULT * atr[i], opts.minStopAt ? opts.minStopAt(X.ct[i], e0) : MIN_STOP), sp;
          if (side === 1) { sp = sw.swl[i]; if (e0 - sp < md) sp = e0 - md; }
          else { sp = sw.swh[i]; if (sp - e0 < md) sp = e0 + md; }
          var dist = Math.abs(e0 - sp);
          if (dist > 0 && !isNaN(dist)) {
            pos = side; entry = e0; stop = sp; entryRisk = risk; units = risk / dist; entryI = i; entryT = X.ct[i];
            events.push({ t: entryT, kind: 'ENTRY', side: side, price: e0, stop: sp, risk: risk, units: units });
          }
        }
      }
    }
    var level = Math.round(risk / step);
    return {
      trades: trades, events: events,
      position: pos === 0 ? null : { side: pos, entry: entry, stop: stop, units: units, risk: entryRisk, entryMs: entryT },
      nextRisk: risk, level: level, lastBarCt: lastI >= 0 ? X.ct[lastI] : null,
      hamaState: lastI >= 0 ? S.state[lastI] : 0,
      stepSize: step
    };
  }
  return { run: run, emaTv: emaTv };
})();
if (typeof module !== 'undefined') module.exports = HAMA;
