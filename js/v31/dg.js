// Directional Unload Grid (1h): EMA200 + Turtle + HAMA Stable (F1+F2+F3), 1m fills.
// Exact port of /workspace/strat_dir_grid/results/dg.py (prep + dgrid, fam=2) with
// common.pine_ema / rma_atr / turtle30 and strat7_3 eng.hama_state.
// Filters use CLOSED 1h bars only; the 4h HAMA lock uses the last fully closed 4h bar BEFORE the 1h bar's 4h bucket.
// A colour flip seen at a 1h close is acted on at the next 1m open. Stops / unloads / adds are checked on every 1m bar.
// Units: $1,000 notional per grid level (scale by base/1000 x multiplier in the app).
(function (root, factory) {
  var DG = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = DG;
  root.DG = DG;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var P = { fam: 2, L: 8, stp: 0.5, satr: 0, rev: 0, um: 2.0, tp: 0, sl: 10.0, xm: 1, notional: 1000,
            emaLen: 200, entryLen: 20, exitLen: 10, tAtrLen: 20, stopMult: 2.0, ccLen: 20, coLen: 25, gap: 0.3, hAtr: 14 };
  var FEE = 0.0004, H = 3600000, H4 = 14400000, MIN = 60000;

  // ---------- small indicator states (JSON-serialisable) ----------
  function ema(n) { return { n: n, s: 0, cnt: 0, v: null }; }
  function emaPush(E, x) {           // common.pine_ema: SMA seed of the first n non-NaN values, NaN input keeps previous
    if (x !== x) return E.v;
    if (E.v === null) { E.s += x; E.cnt += 1; if (E.cnt === E.n) E.v = E.s / E.n; }
    else { var a = 2.0 / (E.n + 1); E.v = a * x + (1 - a) * E.v; }
    return E.v;
  }
  function rma(n) { return { n: n, i: 0, s: 0, v: null, pc: null }; }
  function rmaPush(R, h, l, c) {     // common.rma_atr
    var tr = R.pc === null ? h - l : Math.max(h - l, Math.max(Math.abs(h - R.pc), Math.abs(l - R.pc)));
    if (R.i < R.n - 1) R.s += tr;
    else if (R.i === R.n - 1) { R.s += tr; R.v = R.s / R.n; }
    else R.v = (R.v * (R.n - 1) + tr) / R.n;
    R.i += 1; R.pc = c;
    return R.v;
  }

  // ---------- Turtle 20/10, ATR20, 2x stop, shorts on, 17:00 New York session roll (common.turtle30 on 1h bars) ----------
  var nyFmt = null;
  function sessKey(ms) {
    if (!nyFmt) nyFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hourCycle: 'h23',
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    var o = {}; nyFmt.formatToParts(new Date(ms)).forEach(function (p) { o[p.type] = p.value; });
    var hh = +o.hour % 24, mm = +o.minute, off = (hh * 60 + mm) < 17 * 60 ? -1 : 0;
    return Date.UTC(+o.year, +o.month - 1, +o.day + off) / 86400000;
  }
  function arrMax(a) { var m = -Infinity; for (var i = 0; i < a.length; i++) if (a[i] > m) m = a[i]; return m; }
  function arrMin(a) { var m = Infinity; for (var i = 0; i < a.length; i++) if (a[i] < m) m = a[i]; return m; }
  function remember(a, v, L) { a.push(v); if (a.length > L) a.shift(); }
  function turtleNew() { return { sess: null, sH: null, sL: null, sC: null, pC: null, satr: null, seed: 0, cnt: 0, skip: true,
                                  eH: [], eL: [], xH: [], xL: [], side: 0, fstop: null, stop: null }; }
  function turtleBar(T, b) {
    var sk = sessKey(b.t);
    if (T.sess === null || sk !== T.sess) {
      if (T.sH !== null) {
        if (T.skip) T.skip = false;
        else {
          remember(T.eH, T.sH, P.entryLen); remember(T.eL, T.sL, P.entryLen);
          remember(T.xH, T.sH, P.exitLen); remember(T.xL, T.sL, P.exitLen);
          var tr = T.pC === null ? T.sH - T.sL : Math.max(T.sH - T.sL, Math.abs(T.sH - T.pC), Math.abs(T.sL - T.pC));
          if (T.satr === null) { T.seed += tr; T.cnt += 1; if (T.cnt === P.tAtrLen) T.satr = T.seed / P.tAtrLen; }
          else T.satr = (T.satr * (P.tAtrLen - 1) + tr) / P.tAtrLen;
          T.pC = T.sC;
        }
      }
      T.sH = b.h; T.sL = b.l; T.sC = b.c;
    } else { T.sH = Math.max(T.sH, b.h); T.sL = Math.min(T.sL, b.l); T.sC = b.c; }
    T.sess = sk;
    var ok = T.eH.length === P.entryLen && T.xH.length === P.exitLen && T.satr !== null;
    if (ok) {
      var eh = arrMax(T.eH), el = arrMin(T.eL), lx = arrMin(T.xL), sx = arrMax(T.xH);
      if (T.side === 0) {
        var up = b.h > eh, dn = b.l < el;
        if (up !== dn) {
          if (up) { var c1 = Math.max(eh, b.o) - P.stopMult * T.satr; if (b.l >= c1) { T.side = 1; T.fstop = c1; } }
          else { var c2 = Math.min(el, b.o) + P.stopMult * T.satr; if (b.h <= c2) { T.side = -1; T.fstop = c2; } }
        }
      } else {
        var lvl = T.side === 1 ? Math.max(T.fstop, lx) : Math.min(T.fstop, sx);
        if (T.side === 1 ? b.l < lvl : b.h > lvl) { T.side = 0; T.fstop = null; }
      }
      T.stop = T.side === 1 ? Math.max(T.fstop, lx) : T.side === -1 ? Math.min(T.fstop, sx) : null;
    }
    return ok ? T.side : 0;
  }

  // ---------- filter state ----------
  function newFilters() {
    return {
      n1h: 0, e200: ema(P.emaLen), T: turtleNew(),
      hm: { cc: ema(P.ccLen), co: ema(P.coLen), atr: rma(P.hAtr), prevOC: null, s: 0 },
      h4: { cc: ema(20), co: ema(25), prevOC: null, agg: null, last: null },   // last = { t, g } of last closed 4h bar
      vals: []                                                                // recent closed-1h values [{t, f1, f2, f3, d}]
    };
  }
  function h4Close(G) {
    var a = G.agg; if (!a) return;
    var cc = emaPush(G.cc, (a.o + a.h + a.l + a.c) / 4);
    var co = emaPush(G.co, G.prevOC === null ? NaN : G.prevOC);
    G.prevOC = (a.o + a.c) / 2;
    G.last = { t: a.t, g: cc !== null && co !== null && cc >= co };
    G.agg = null;
  }
  // Feed one CLOSED 1h bar {t,o,h,l,c} (in time order). Returns its combined value (+1 / -1 / 0).
  function closeHour(st, b) {
    var F = st.F;
    if (F.lastT != null && b.t <= F.lastT) return null;        // already processed
    // F1: close vs EMA200 (0 for the first 200 bars)
    var e = emaPush(F.e200, b.c); F.n1h += 1;
    var f1 = F.n1h <= P.emaLen || e === null ? 0 : (b.c > e ? 1 : b.c < e ? -1 : 0);
    // F2: Turtle on 1h bars (0 until ready)
    var f2 = turtleBar(F.T, b);
    // F3: HAMA Stable 1h, gap 0.3 x ATR14, lock = green of the last closed 4h bar before this bar's 4h bucket
    var G = F.h4, bucket = Math.floor(b.t / H4) * H4;
    if (G.agg && G.agg.t < bucket) h4Close(G);
    var lock = !!(G.last && G.last.t < bucket && G.last.g);
    var hm = F.hm;
    var cc = emaPush(hm.cc, (b.o + b.h + b.l + b.c) / 4);
    var co = emaPush(hm.co, hm.prevOC === null ? NaN : hm.prevOC);
    hm.prevOC = (b.o + b.c) / 2;
    var atr = rmaPush(hm.atr, b.h, b.l, b.c);
    var okc = cc !== null && co !== null;
    var want = okc && cc >= co, gp = okc && atr !== null && Math.abs(cc - co) >= P.gap * atr;
    var canG = want && gp && lock, canR = !want && gp && !lock;
    if (hm.s === 0 && okc) hm.s = want ? 1 : -1;
    else if (hm.s === -1 && canG) hm.s = 1;
    else if (hm.s === 1 && canR) hm.s = -1;
    var f3 = hm.s;
    // 4h aggregation after the decision, so this bar's own 4h bucket is never used for its lock
    if (!G.agg) G.agg = { t: bucket, o: b.o, h: b.h, l: b.l, c: b.c };
    else { G.agg.h = Math.max(G.agg.h, b.h); G.agg.l = Math.min(G.agg.l, b.l); G.agg.c = b.c; }
    var d = (f1 === f2 && f2 === f3) ? f1 : 0;
    F.vals.push({ t: b.t, f1: f1, f2: f2, f3: f3, d: d }); if (F.vals.length > 6) F.vals.shift();
    F.lastT = b.t; F.lastBar = { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c };
    return d;
  }
  // Filter value seen at the CLOSE of the minute starting at t: last closed 1h bar.
  function filterAt(st, t) {
    var co = Math.floor((t + MIN) / H) * H - H, v = st.F.vals;
    for (var i = v.length - 1; i >= 0; i--) if (v[i].t <= co) return v[i];
    return null;
  }

  function newState() {
    return { F: newFilters(), g: { ent: [], qty: [], side: 0, ref: 0, lastAdd: 0, extreme: 0, armed: 0, pend: 0, pendX: false,
             realized: 0, fee: 0, fills: 0, maxlev: 0, t0: null, e0: null }, dp: null, lastMinT: null, events: [] };
  }
  function nlev(g) { return g.ent.length; }
  function avgOf(g) { var sq = 0, nq = 0; for (var k = 0; k < g.ent.length; k++) { sq += g.ent[k] * g.qty[k]; nq += g.qty[k]; } return sq / nq; }
  function basket(g, t, px, why) {
    return { entry_t: g.t0, exit_t: t, dir: g.side, entry_px: g.e0, exit_px: px, pnl: g.realized, fee: g.fee, net: g.realized - g.fee,
             fills: g.fills, maxlev: g.maxlev, why: why };
  }
  var WHY = { 1: 'STOP', 2: 'TP', 3: 'UNLOADED', 4: 'COLOUR', 5: 'END' };

  // Process one CLOSED 1m bar m = {t,o,h,l,c}. closeHour() for the hour that closes with this minute must be called first.
  // opts.noDecision skips the colour decision at this minute (dg.py skips i == 0).
  // Returns { trades: [...], events: [...], open, mw, mc } (mw/mc = worst / close mark-to-market of the open basket, $ per 1000/level).
  function stepMin(st, m, opts) {
    opts = opts || {};
    if (st.lastMinT != null && m.t <= st.lastMinT) return { trades: [], events: [], skipped: true };
    var g = st.g, oi = m.o, hi = m.h, li = m.l, trades = [], ev = [], N = P.notional, k, fx;
    if (g.pendX && nlev(g) > 0) {
      for (k = 0; k < g.ent.length; k++) { g.realized += g.side * g.qty[k] * (oi - g.ent[k]); g.fee += FEE * g.qty[k] * oi; }
      var b4 = basket(g, m.t, oi, 4); b4.fills = g.fills + g.ent.length; trades.push(b4);
      ev.push({ kind: 'EXIT', t: m.t, dir: g.side, price: oi, why: 'COLOUR', net: b4.net });
      g.ent = []; g.qty = [];
    }
    g.pendX = false;
    if (g.pend !== 0 && nlev(g) === 0) {
      g.side = g.pend; g.ent = [oi]; g.qty = [N / oi]; g.lastAdd = oi; g.extreme = oi; g.armed = 0;
      g.realized = 0; g.fee = FEE * N; g.fills = 1; g.maxlev = 1; g.t0 = m.t; g.e0 = oi;
      ev.push({ kind: 'ENTRY', t: m.t, dir: g.side, price: oi, lev: 1 });
    }
    g.pend = 0;
    if (nlev(g) > 0) {
      var side = g.side, step = (P.stp / 100.0) * g.lastAdd, avg = avgOf(g), closed = false, px = 0, why = 0;
      if (P.sl > 0) {
        var sp = avg * (1 - side * P.sl / 100);
        if ((side > 0 && li <= sp) || (side < 0 && hi >= sp)) { px = side > 0 ? Math.min(sp, oi) : Math.max(sp, oi); closed = true; why = 1; }
      }
      if (!closed && P.tp > 0) {
        var tpp = avg * (1 + side * P.tp / 100);
        if ((side > 0 && hi >= tpp) || (side < 0 && li <= tpp)) { px = side > 0 ? Math.max(tpp, oi) : Math.min(tpp, oi); closed = true; why = 2; }
      }
      if (closed) {
        for (k = 0; k < g.ent.length; k++) { g.realized += side * g.qty[k] * (px - g.ent[k]); g.fee += FEE * g.qty[k] * px; }
        var bs = basket(g, m.t, px, why); bs.fills = g.fills + g.ent.length; trades.push(bs);
        ev.push({ kind: 'EXIT', t: m.t, dir: side, price: px, why: WHY[why], net: bs.net });
        g.ent = []; g.qty = [];
      } else if (step > 0) {
        var did = false;
        if (g.armed === 0) {
          var ul = g.extreme - side * (P.um * step);
          if ((side > 0 && li <= ul) || (side < 0 && hi >= ul)) {
            fx = side > 0 ? Math.min(ul, oi) : Math.max(ul, oi);
            k = g.ent.length - 1;
            g.realized += side * g.qty[k] * (fx - g.ent[k]); g.fee += FEE * g.qty[k] * fx; g.fills += 1;
            g.ent.pop(); g.qty.pop(); did = true;
            if (g.ent.length > 0) {
              g.lastAdd = g.ent[g.ent.length - 1]; g.extreme = fx; g.armed = 1;
              ev.push({ kind: 'UNLOAD', t: m.t, dir: side, price: fx, lev: g.ent.length });
            } else {
              var bu = basket(g, m.t, fx, 3); trades.push(bu);
              ev.push({ kind: 'EXIT', t: m.t, dir: side, price: fx, why: 'UNLOADED', net: bu.net });
            }
          }
        }
        if (!did && g.ent.length > 0) {
          while (g.ent.length < P.L) {
            var lv = g.lastAdd + side * step;
            if (!((side > 0 && hi >= lv) || (side < 0 && li <= lv))) break;
            fx = side > 0 ? Math.max(lv, oi) : Math.min(lv, oi);
            g.ent.push(fx); g.qty.push(N / fx); g.lastAdd = fx; g.fee += FEE * N; g.fills += 1;
            step = (P.stp / 100.0) * g.lastAdd;
            ev.push({ kind: 'ADD', t: m.t, dir: side, price: fx, lev: g.ent.length });
          }
        }
        if (g.ent.length > 0) {
          if (side > 0 && hi > g.extreme) { g.extreme = hi; g.armed = 0; }
          if (side < 0 && li < g.extreme) { g.extreme = li; g.armed = 0; }
        }
      }
      if (g.ent.length > g.maxlev) g.maxlev = g.ent.length;
    }
    var mw = 0, mc = 0, open = g.ent.length > 0;
    if (open) {
      var wp = g.side > 0 ? li : hi; mw = g.realized - g.fee; mc = mw;
      for (k = 0; k < g.ent.length; k++) { mw += g.side * g.qty[k] * (wp - g.ent[k]); mc += g.side * g.qty[k] * (m.c - g.ent[k]); }
    }
    // ---- colour decision at this minute's close (uses the last closed 1h bar) ----
    var fv = filterAt(st, m.t), d = fv ? fv.d : 0;
    if (!opts.noDecision && st.dp !== null) {
      var dp = st.dp;
      if (g.ent.length > 0) { if ((P.xm === 0 && d !== g.ref) || (P.xm === 1 && d === -g.ref)) g.pendX = true; }
      if (d !== dp && d !== 0 && (g.ent.length === 0 || g.pendX)) {
        g.pend = d; g.ref = d;
        ev.push({ kind: 'SIGNAL', t: m.t + MIN, dir: d, price: m.c });
      }
    }
    st.dp = d; st.lastMinT = m.t; st.lastPx = m.c;
    return { trades: trades, events: ev, open: open, mw: mw, mc: mc };
  }
  // Mark the open basket at price px as if closed there (what dg.py does at the end of data, why = 5).
  function markOpen(st, t, px) {
    var g = st.g; if (!g.ent.length) return null;
    var r = g.realized, f = g.fee;
    for (var k = 0; k < g.ent.length; k++) { r += g.side * g.qty[k] * (px - g.ent[k]); f += FEE * g.qty[k] * px; }
    return { entry_t: g.t0, exit_t: t, dir: g.side, entry_px: g.e0, exit_px: px, pnl: r, fee: f, net: r - f, fills: g.fills + g.ent.length, maxlev: g.maxlev, why: 5 };
  }
  // Live levels for display: next add, unload trigger, stop.
  function levels(st) {
    var g = st.g; if (!g.ent.length) return null;
    var side = g.side, step = (P.stp / 100.0) * g.lastAdd, avg = avgOf(g);
    return { side: side, n: g.ent.length, avg: avg, stop: avg * (1 - side * P.sl / 100),
             nextAdd: g.ent.length < P.L ? g.lastAdd + side * step : null,
             unload: g.armed === 0 ? g.extreme - side * (P.um * step) : null, extreme: g.extreme, armed: g.armed,
             ent: g.ent.slice(), qty: g.qty.slice() };
  }


  // Live / catch-up driver. hours: closed 1h bars, mins: closed 1m bars (both sorted, any overlap with processed data is skipped).
  // Each minute is processed only after the 1h bar that closes with it has been fed, so the filters never see an unfinished hour.
  // Stops (returns waiting=true) when the next needed 1h bar is not available yet.
  function catchup(st, hours, mins) {
    var j = 0, out = { trades: [], events: [], nMin: 0, nHour: 0, waiting: false };
    var lastHour = hours.length ? hours[hours.length - 1].t : -Infinity;
    for (var i = 0; i < mins.length; i++) {
      var m = mins[i];
      if (st.lastMinT != null && m.t <= st.lastMinT) continue;
      var need = Math.floor((m.t + MIN) / H) * H - H;
      while (j < hours.length && hours[j].t <= need) {
        if (st.F.lastT == null || hours[j].t > st.F.lastT) { closeHour(st, hours[j]); out.nHour++; }
        j++;
      }
      if (st.F.lastT == null || st.F.lastT < need) {
        if (lastHour > need) { /* the exchange has no bar for that hour: keep the last closed value */ }
        else { out.waiting = true; break; }
      }
      var r = stepMin(st, m);
      r.trades.forEach(function (x) { out.trades.push(x); });
      r.events.forEach(function (x) { out.events.push(x); });
      out.nMin++;
    }
    return out;
  }

  // ---- sizing per coin from the closed-basket sequence: kind 'lin' -> 1 + X*k, 'pct' -> (1+X)^k, k = floor(lossesInRow / N), cap ----
  function multFor(rule, L) {
    var k = Math.floor(L / rule.N);
    return rule.kind === 'flat' ? 1 : rule.kind === 'pct' ? Math.min(rule.cap, Math.pow(1 + rule.X, k)) : Math.min(rule.cap, 1 + rule.X * k);
  }
  function sizingFrom(trades, rule) {
    var L = 0, out = [];
    for (var i = 0; i < trades.length; i++) {
      var m = multFor(rule, L), sc = rule.base * m / P.notional;
      out.push({ m: m, level: rule.base * m, usd: trades[i].net * sc, usd0: trades[i].pnl * sc, fee: trades[i].fee * sc });
      L = trades[i].net < 0 ? L + 1 : 0;
    }
    var m2 = multFor(rule, L), steps = [], s = 0, mm = multFor(rule, 0);
    while (true) { steps.push(mm); s += rule.N; var nx = multFor(rule, s); if (nx === mm || steps.length > 30) break; mm = nx; }
    return { perTrade: out, lossesInRow: L, mult: m2, level: rule.base * m2, step: steps.indexOf(m2) + 1, nSteps: steps.length, steps: steps };
  }
  return { P: P, FEE: FEE, H: H, H4: H4, newState: newState, closeHour: closeHour, stepMin: stepMin, filterAt: filterAt,
           markOpen: markOpen, catchup: catchup, levels: levels, sizingFrom: sizingFrom, multFor: multFor, sessKey: sessKey, WHY: WHY };
});
