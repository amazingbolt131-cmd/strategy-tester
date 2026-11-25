// Strategy 5: Extreme Reversal + 30m Turtle Bias (Reversal Turtle MTF), honest version.
// Exact port of /workspace/strat5_6/s5.py (run, honest mode) + common.turtle30 + ER indicators.
// Signals on closed 15m candles; 30m bias from the last CLOSED 30m bar before the 15m bar's 30m bucket.
// Entries/adds fill at the next 1m open (never on the same candle). Exits on 1m: gap at open, stop before TP in one minute.
(function (root, factory) {
  var S5 = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = S5;
  root.S5 = S5;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var P = { lenBB: 200, turtleLen: 20, proxPct: 6.0, atrLen: 14, lookbackBars: 1000, targetATRMult: 70.0,
            tp1: 6.0, tp2: 3.0, thrAdd: 3.0, entryLen: 20, exitLen: 10, tAtrLen: 20, stopMult: 2.0 };
  var FEE = 0.0004, M15 = 900000, M30 = 1800000;
  var nyFmt = null;
  function nyParts(ms) {
    if (!nyFmt) nyFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hourCycle: 'h23',
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    var o = {}; nyFmt.formatToParts(new Date(ms)).forEach(function (p) { o[p.type] = p.value; });
    return { y: +o.year, m: +o.month, d: +o.day, hh: +o.hour % 24, mm: +o.minute };
  }
  function sessKey(ms) {
    var p = nyParts(ms), off = (p.hh * 60 + p.mm) < 17 * 60 ? -1 : 0;
    return Date.UTC(p.y, p.m - 1, p.d + off) / 86400000;
  }
  function arrMax(a) { var m = -Infinity; for (var i = 0; i < a.length; i++) if (a[i] > m) m = a[i]; return m; }
  function arrMin(a) { var m = Infinity; for (var i = 0; i < a.length; i++) if (a[i] < m) m = a[i]; return m; }
  function remember(a, v, L) { a.push(v); if (a.length > L) a.shift(); }

  // ---- 30m Turtle engine (one completed 30m bar) ----
  function turtleBar(T, b) {
    var sk = sessKey(b.t);
    var isNew = T.sess === null || sk !== T.sess;
    if (isNew) {
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
    T.hist.push({ t: b.t, side: T.side, ready: ok, stop: T.stop });
    if (T.hist.length > 4) T.hist.shift();
  }
  function biasFor(T, t15) {
    var k = Math.floor(t15 / M30) * M30;
    for (var i = T.hist.length - 1; i >= 0; i--) if (T.hist[i].t < k) return T.hist[i];
    return null;
  }

  // ---- 15m indicators (one closed 15m bar) ----
  function indBar(I, b) {
    var pc = I.prevClose;
    var tr = pc === null ? b.h - b.l : Math.max(b.h - b.l, Math.abs(b.h - pc), Math.abs(b.l - pc));
    var a = 1 / P.atrLen;
    // The published catch-up leaves atr null forever (it assumes a seeded snapshot).
    // A fresh backtest SMA-seeds the first atrLen true ranges, then uses Wilder,
    // the same seed the 30m Turtle ATR in this file already uses.
    if (I.atr === null) {
      I._seed = (I._seed || 0) + tr;
      I._cnt = (I._cnt || 0) + 1;
      if (I._cnt === P.atrLen) I.atr = I._seed / P.atrLen;
    } else I.atr = a * tr + (1 - a) * I.atr;
    I.prevClose = b.c;
    remember(I.closes, b.c, P.lenBB); remember(I.highs, b.h, P.turtleLen); remember(I.lows, b.l, P.turtleLen);
    var s = 0; for (var i = 0; i < I.closes.length; i++) s += I.closes[i];
    var basis = I.closes.length === P.lenBB ? s / P.lenBB : NaN;
    var hi = arrMax(I.highs), lo = arrMin(I.lows);
    var LT = b.c < basis && b.c <= lo * (1 + P.proxPct / 100);
    var ST = b.c > basis && b.c >= hi * (1 - P.proxPct / 100);
    if ((LT || ST) && I.atr !== null) { I.trig.push(I.atr); if (I.trig.length > P.lookbackBars) I.trig.shift(); }
    var avg = NaN;
    if (I.trig.length === P.lookbackBars) { var t = 0; for (var k = 0; k < I.trig.length; k++) t += I.trig[k]; avg = t / P.lookbackBars; }
    var AD = (isNaN(avg) ? I.atr : avg) * (P.targetATRMult / 3);
    return { LT: LT, ST: ST, AD: AD, basis: basis };
  }

  function newState(snap) {
    var s = JSON.parse(JSON.stringify(snap));
    s.pos = s.pos || null; s.pend = s.pend || null; s.pendAdd = s.pendAdd || null; s.half = s.half || null;
    return s;
  }

  function closeTr(P0, which, px) {
    var q = which === 0 ? P0.q0 : P0.q1, e = which === 0 ? P0.e0 : P0.e1;
    P0.pnl += P0.dir * q * (px - e); P0.fee += FEE * q * px; P0.lastPx = px;
    if (which === 0) P0.op0 = false; else P0.op1 = false;
  }
  function avgOf(p) {
    var qq = (p.op0 ? p.q0 : 0) + (p.op1 ? p.q1 : 0);
    return ((p.op0 ? p.q0 * p.e0 : 0) + (p.op1 ? p.q1 * p.e1 : 0)) / qq;
  }

  // Process one closed 15m bar `b` (t,o,h,l,c) and its 1m bars `mins` (t,o,h,l,c), in time order.
  // Returns { events: [...], trades: [...] } for this bar. Mutates st.
  function step15(st, b, mins) {
    var ev = [], trades = [];
    for (var i = 0; i < mins.length; i++) {
      var m = mins[i];
      if (st.pend && !st.pos) {
        var d = st.pend.dir, e0 = m.o, q0 = st.pend.q;
        st.pos = { dir: d, e0: e0, q0: q0, op0: true, q1: 0, e1: 0, op1: false, addUsed: false, pnl: 0, fee: FEE * q0 * e0,
                   mae: 0, entT: m.t, notl: q0 * e0, stp: e0 - d * st.pend.ad, lim0: e0 * (1 + d * P.tp1 / 100), lim1: 0, adT: null };
        ev.push({ kind: 'ENTRY', t: m.t, dir: d, price: e0, q: q0, stop: st.pos.stp, tp: st.pos.lim0 });
        st.pend = null;
      } else if (st.pendAdd && st.pos) {
        var p = st.pos;
        p.e1 = m.o; p.q1 = p.q0 * p.e0 / st.pendAdd.c; p.op1 = true; p.addUsed = true; p.fee += FEE * p.q1 * p.e1; p.notl += p.q1 * p.e1;
        var qq = (p.op0 ? p.q0 : 0) + p.q1, avg = ((p.op0 ? p.q0 * p.e0 : 0) + p.q1 * p.e1) / qq;
        p.stp = avg - p.dir * st.pendAdd.ad; p.lim0 = avg * (1 + p.dir * P.tp1 / 100); p.lim1 = avg * (1 + p.dir * P.tp2 / 100); p.adT = m.t;
        ev.push({ kind: 'ADD', t: m.t, dir: p.dir, price: p.e1, q: p.q1, stop: p.stp, tp: p.lim1 });
      }
      st.pendAdd = null; if (st.pos) st.pend = null;
      var p2 = st.pos;
      if (p2) {
        var closed = false, why = '', d2 = p2.dir;
        if ((d2 > 0 && m.o <= p2.stp) || (d2 < 0 && m.o >= p2.stp)) {
          if (p2.op0) closeTr(p2, 0, m.o); if (p2.op1) closeTr(p2, 1, m.o); closed = true; why = 'STOP';
        } else {
          if (p2.op0 && ((d2 > 0 && m.o >= p2.lim0) || (d2 < 0 && m.o <= p2.lim0))) { closeTr(p2, 0, m.o); ev.push({ kind: 'TP', t: m.t, dir: d2, price: m.o, tranche: 1 }); }
          if (p2.op1 && ((d2 > 0 && m.o >= p2.lim1) || (d2 < 0 && m.o <= p2.lim1))) { closeTr(p2, 1, m.o); ev.push({ kind: 'TP', t: m.t, dir: d2, price: m.o, tranche: 2 }); }
          if (!(p2.op0 || p2.op1)) { closed = true; why = 'TP'; }
          else {
            var wp = d2 > 0 ? m.l : m.h;
            var cur = p2.pnl + (p2.op0 ? d2 * p2.q0 * (wp - p2.e0) : 0) + (p2.op1 ? d2 * p2.q1 * (wp - p2.e1) : 0);
            if (cur < p2.mae) p2.mae = cur;
            var hitS = d2 > 0 ? m.l <= p2.stp : m.h >= p2.stp;
            if (hitS) { var sp = p2.stp; if (p2.op0) closeTr(p2, 0, sp); if (p2.op1) closeTr(p2, 1, sp); closed = true; why = 'STOP'; }
            else {
              if (p2.op0 && ((d2 > 0 && m.h >= p2.lim0) || (d2 < 0 && m.l <= p2.lim0))) { closeTr(p2, 0, p2.lim0); ev.push({ kind: 'TP', t: m.t, dir: d2, price: p2.lim0, tranche: 1 }); }
              if (p2.op1 && ((d2 > 0 && m.h >= p2.lim1) || (d2 < 0 && m.l <= p2.lim1))) { closeTr(p2, 1, p2.lim1); ev.push({ kind: 'TP', t: m.t, dir: d2, price: p2.lim1, tranche: 2 }); }
              if (!(p2.op0 || p2.op1)) { closed = true; why = 'TP'; }
            }
          }
        }
        if (closed) {
          var tr = { entry_t: p2.entT, exit_t: m.t, dir: d2, entry_px: p2.e0, add_px: p2.addUsed ? p2.e1 : null, add_t: p2.adT,
                     exit_px: p2.lastPx,
                     reason: why, pnl_R: p2.pnl, fee_R: p2.fee, net_R: p2.pnl - p2.fee, mae_R: p2.mae, tranches: p2.addUsed ? 2 : 1,
                     q0: p2.q0, q1: p2.q1, notl: p2.notl };
          trades.push(tr);
          ev.push({ kind: 'EXIT', t: m.t, dir: d2, why: why, net_R: tr.net_R, price: tr.exit_px });
          st.pos = null;
        }
      }
    }
    // ---- 15m close decision (uses bias from the last closed 30m bar before this bar's bucket) ----
    var bi = biasFor(st.T, b.t);
    var aL = !!(bi && bi.ready && bi.side === 1), aS = !!(bi && bi.ready && bi.side === -1);
    var ind = indBar(st.I, b);
    var LT = ind.LT && aL, ST = ind.ST && aS, AD = ind.AD;
    var sig = null;
    if (!st.pos) {
      var dd = LT ? 1 : ST ? -1 : 0;
      if (dd !== 0 && AD === AD && AD !== null) { st.pend = { dir: dd, q: 1 / AD, ad: AD }; sig = { kind: 'SIGNAL', t: b.t + M15, dir: dd, price: b.c, stop: b.c - dd * AD, tp: b.c * (1 + dd * P.tp1 / 100) }; }
    } else {
      var p3 = st.pos, av = avgOf(p3), mv = (b.c / av - 1) * 100 * p3.dir;
      var okd = (p3.dir > 0 && aL) || (p3.dir < 0 && aS);
      if (!p3.addUsed && mv >= P.thrAdd && okd) { st.pendAdd = { ad: AD, c: b.c }; sig = { kind: 'ADD_SIGNAL', t: b.t + M15, dir: p3.dir, price: b.c }; }
      p3.stp = av - p3.dir * AD; p3.lim0 = av * (1 + p3.dir * P.tp1 / 100); p3.lim1 = av * (1 + p3.dir * P.tp2 / 100);
    }
    if (sig) ev.push(sig);
    // ---- 30m aggregation (after the decision, so a just-closed 30m bar is not used for this bar) ----
    if (b.t % M30 === 0) st.half = { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c };
    else {
      var t30 = b.t - M15, hb = st.half && st.half.t === t30 ? st.half : null;
      var bar30 = hb ? { t: t30, o: hb.o, h: Math.max(hb.h, b.h), l: Math.min(hb.l, b.l), c: b.c } : { t: t30, o: b.o, h: b.h, l: b.l, c: b.c };
      turtleBar(st.T, bar30); st.half = null;
    }
    st.last15T = b.t;
    st.bias = { side: bi ? bi.side : 0, ready: !!(bi && bi.ready), stop: bi ? bi.stop : null, t: bi ? bi.t : null };
    return { events: ev, trades: trades };
  }

  // ---- sizing: multiplier from the closed-trade sequence (per coin, reset after a win) ----
  // rule: { N: losses per step, X: growth per step (1.0 = double), cap: max multiple, base: $ risk at 1x }
  function sizingFrom(trades, rule) {
    var L = 0, out = [];
    for (var i = 0; i < trades.length; i++) {
      var k = Math.floor(L / rule.N), m = Math.min(rule.cap, Math.pow(1 + rule.X, k));
      out.push({ m: m, risk: rule.base * m, usd: trades[i].net_R * rule.base * m, usd0: trades[i].pnl_R * rule.base * m });
      L = trades[i].net_R < 0 ? L + 1 : 0;
    }
    var k2 = Math.floor(L / rule.N), m2 = Math.min(rule.cap, Math.pow(1 + rule.X, k2));
    var steps = []; var mm = 1, s = 0; while (true) { steps.push(mm); s++; var nx = Math.min(rule.cap, Math.pow(1 + rule.X, s)); if (nx === mm || s > 20) break; mm = nx; }
    return { perTrade: out, lossesInRow: L, mult: m2, risk: rule.base * m2, step: steps.indexOf(m2) + 1, nSteps: steps.length, steps: steps };
  }
  return { P: P, FEE: FEE, step15: step15, newState: newState, sizingFrom: sizingFrom, sessKey: sessKey, biasFor: biasFor };
});

