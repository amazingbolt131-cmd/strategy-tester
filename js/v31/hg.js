// HAMA Grid + Count: exact port of /workspace/sym38/app_multi/hg/export_seed.py Inc.
// Closed bars only, no lookahead. Fills on 1m after the signal close.
(function (root, factory) {
  var HG = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = HG;
  root.HG = HG;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var W0 = 1763589600000; // 2025-11-20 00:00 Europe/Kyiv
  var FEE = 0.0004;
  var UNIT = 1000;
  var WHY = { 1: 'STOP', 2: 'TP' };

  var CFG = {
    g5m: {
      id: 'g5m', name: '5m HAMA Grid + Count',
      sig_min: 5, lock_min: 0,
      count_min: 60, count_win: '3d', count_tf: '1h', thr: 0.65,
      step: 0.0075, maxu: 5, tp: 0.06, stop: 0.045, fee: FEE, unit: UNIT
    },
    g1m: {
      id: 'g1m', name: '1m HAMA Grid + Count',
      sig_min: 1, lock_min: 15,
      count_min: 240, count_win: '5d', count_tf: '4h', thr: 0.65,
      step: 0.003, maxu: 5, tp: 0.06, stop: 0.018, fee: FEE, unit: UNIT
    }
  };

  var WIN_DAYS = { '1d': 1, '2d': 2, '3d': 3, '5d': 5, '1w': 7, '2w': 14 };
  var TF_MIN = { '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240 };
  function nBars(win, ctf) { return (WIN_DAYS[win] * 1440 / TF_MIN[ctf]) | 0; }

  function isNan(x) { return x == null || (typeof x === 'number' && isNaN(x)); }
  function ms(t) { return t == null || t === '' ? null : Math.trunc(Number(t)); }

  function Ema(n, alpha) {
    return { n: n, a: (alpha == null ? 2 / (n + 1) : alpha), s: 0, cnt: 0, v: null };
  }
  function emaPush(E, x) {
    if (isNan(x)) return E.v; // keep prev, do not seed
    if (E.cnt < E.n) {
      E.s += +x;
      E.cnt += 1;
      if (E.cnt === E.n) E.v = E.s / E.n;
    } else {
      E.v = E.a * +x + (1 - E.a) * E.v;
    }
    return E.v;
  }
  function emaDump(E) { return { n: E.n, a: E.a, s: E.s, cnt: E.cnt, v: E.v }; }
  function emaLoad(d) {
    var E = Ema(d.n, d.a);
    E.s = d.s; E.cnt = d.cnt; E.v = (d.v == null ? null : d.v);
    return E;
  }

  function Hama() {
    return { cc: Ema(20), co: Ema(25), atr: Ema(14, 1 / 14), prev_mid: null, prev_tr_c: null };
  }
  function hamaPush(H, o, h, l, c) {
    var mid = 0.5 * (o + c);
    var ohlc4 = 0.25 * (o + h + l + c);
    var cc = emaPush(H.cc, ohlc4);
    var coIn = (H.prev_mid == null ? NaN : H.prev_mid);
    var co = emaPush(H.co, coIn);
    H.prev_mid = mid;
    var tr;
    if (H.prev_tr_c == null) tr = h - l;
    else {
      var pc = H.prev_tr_c;
      tr = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
    }
    var atr = emaPush(H.atr, tr);
    H.prev_tr_c = c;
    return { cc: cc, co: co, atr: atr };
  }
  function hamaDump(H) {
    return { cc: emaDump(H.cc), co: emaDump(H.co), atr: emaDump(H.atr), prev_mid: H.prev_mid, prev_tr_c: H.prev_tr_c };
  }
  function hamaLoad(d) {
    var H = Hama();
    H.cc = emaLoad(d.cc); H.co = emaLoad(d.co); H.atr = emaLoad(d.atr);
    H.prev_mid = d.prev_mid == null ? null : d.prev_mid;
    H.prev_tr_c = d.prev_tr_c == null ? null : d.prev_tr_c;
    return H;
  }

  function allowMask(gs, dr, thr) {
    // Neutral / nan share = allow both.
    if (isNan(gs)) return true;
    var longOk = gs >= thr, shortOk = (1 - gs) >= thr;
    if (dr === 1 && shortOk) return false;
    if (dr === -1 && longOk) return false;
    return true;
  }

  function biasFromShare(gs, thr) {
    if (isNan(gs)) return 0;
    if (gs >= thr) return 1;
    if ((1 - gs) >= thr) return -1;
    return 0;
  }

  function newInc(cfg) {
    return {
      cfg: cfg,
      sig: Hama(),
      cnt: Hama(),
      lock_h: cfg.lock_min ? Hama() : null,
      s: 0,
      started: false,
      colors: [],
      lock_hist: [],
      W: nBars(cfg.count_win, cfg.count_tf),
      agg: {},
      grid: null,
      trades: [],
      last_t: null,
      last_c: null
    };
  }

  function countShare(inc) {
    if (inc.colors.length < inc.W) return NaN;
    var w = inc.colors.slice(-inc.W);
    for (var i = 0; i < w.length; i++) if (w[i] === 0) return NaN;
    var g = 0;
    for (i = 0; i < w.length; i++) if (w[i] === 1) g++;
    return g / inc.W;
  }

  function lockAt(inc, otSig) {
    if (!inc.cfg.lock_min) return 1;
    var step = inc.cfg.lock_min * 60000;
    var k = Math.floor(otSig / step) * step;
    var col = 0;
    var hist = inc.lock_hist;
    for (var i = 0; i < hist.length; i++) {
      var t = hist[i][0];
      if (t < k) col = hist[i][1];
      else break;
    }
    return col;
  }

  function closeTf(inc, tfMin, bucket) {
    var o = bucket.o, h = bucket.h, l = bucket.l, c = bucket.c, t = bucket.t;
    if (tfMin === inc.cfg.count_min) {
      var r = hamaPush(inc.cnt, o, h, l, c);
      var col = (isNan(r.cc) || isNan(r.co)) ? 0 : (r.cc >= r.co ? 1 : -1);
      inc.colors.push(col | 0);
      if (inc.colors.length > inc.W + 8) inc.colors = inc.colors.slice(-(inc.W + 8));
    }
    if (inc.lock_h && tfMin === inc.cfg.lock_min) {
      r = hamaPush(inc.lock_h, o, h, l, c);
      col = (isNan(r.cc) || isNan(r.co)) ? 0 : (r.cc >= r.co ? 1 : 0);
      inc.lock_hist.push([t, col]);
      if (inc.lock_hist.length > 32) inc.lock_hist = inc.lock_hist.slice(-32);
    }
    if (tfMin === inc.cfg.sig_min) return signalBar(inc, t, o, h, l, c);
    return [0, 0];
  }

  function signalBar(inc, t, o, h, l, c) {
    var r = hamaPush(inc.sig, o, h, l, c);
    if (isNan(r.cc) || isNan(r.co)) return [0, 0];
    var lock = lockAt(inc, t);
    var wantG = r.cc >= r.co;
    var gapOk = !isNan(r.atr) && Math.abs(r.cc - r.co) >= 0.3 * r.atr;
    var useHTF = !!inc.cfg.lock_min;
    var hg = lock === 1;
    var condG = wantG && gapOk && (!useHTF || hg);
    var condR = (!wantG) && gapOk && (!useHTF || !hg);
    var prev = inc.s;
    if (!inc.started) {
      inc.s = wantG ? 1 : -1;
      inc.started = true;
    } else if (inc.s === -1 && condG) inc.s = 1;
    else if (inc.s === 1 && condR) inc.s = -1;
    if (prev !== 0 && inc.s !== prev) {
      if (t < W0) return [0, 0];
      var gs = countShare(inc);
      var allowed = allowMask(gs, inc.s, inc.cfg.thr);
      var d = inc.s | 0;
      return [d, allowed ? d : 0];
    }
    return [0, 0];
  }

  function fills(inc, t, o, h, l, c) {
    var g = inc.grid;
    if (!g) return;
    var vd = g.vd, p = g.p, cfg = inc.cfg;
    var hs = p * (1 - vd * (cfg.maxu + 1) * cfg.step);
    var tpp = p * (1 + vd * cfg.tp);
    var done = false, xp = null;
    if (g.qty > 0) {
      if ((vd === 1 && l <= hs) || (vd === -1 && h >= hs)) {
        xp = (vd === 1 ? Math.min(hs, o) : Math.max(hs, o));
        done = true;
      } else if ((vd === 1 && h >= tpp) || (vd === -1 && l <= tpp)) {
        xp = (vd === 1 ? Math.max(tpp, o) : Math.min(tpp, o));
        done = true;
      }
    }
    if (!done && g.nu < cfg.maxu) {
      var L = p * (1 - vd * g.lvl * cfg.step);
      var fx = null;
      if (vd === 1 && l <= L) fx = Math.min(L, o);
      else if (vd === -1 && h >= L) fx = Math.max(L, o);
      if (fx != null) {
        g.qty += 1;
        g.cost += fx;
        g.nu += 1;
        g.lvl += 1;
        g.fees += cfg.fee;
        if (g.first == null) g.first = t;
      }
    }
    if (done && g.qty > 0) {
      var avg = g.cost / g.qty;
      var gu = vd * (xp / avg - 1) * g.qty * cfg.unit;
      var fees = g.fees + g.qty * cfg.fee;
      var stopped = (vd === 1 && l <= hs) || (vd === -1 && h >= hs);
      inc.trades.push({
        entry_t: ms(g.first), exit_t: ms(t), dir: vd | 0,
        entry_px: avg, exit_px: xp,
        pnl: gu, fee: fees * cfg.unit, net: gu - fees * cfg.unit,
        fills: g.nu | 0, maxlev: g.nu | 0,
        why: stopped ? 1 : 2,
        flip_p: p
      });
      inc.grid = null;
    }
  }

  function onMin(inc, t, o, h, l, c) {
    if (inc.last_t != null && t <= inc.last_t) return;
    var close = t + 60000;
    var cfg = inc.cfg;
    var tfs = {};
    tfs[cfg.count_min] = 1;
    if (cfg.lock_min) tfs[cfg.lock_min] = 1;
    tfs[cfg.sig_min] = 1;
    var keys = Object.keys(tfs).map(Number).filter(function (x) { return x > 0; }).sort(function (a, b) { return b - a; });
    var pending = null;
    for (var i = 0; i < keys.length; i++) {
      var tf = keys[i];
      var step = tf * 60000;
      var B = Math.floor(t / step) * step;
      var ag = inc.agg[tf];
      var keepInc = (tf === cfg.lock_min && tf !== cfg.sig_min && tf !== cfg.count_min);
      if (ag && ag.t !== B) {
        if (keepInc || ag.n === tf) {
          if (tf !== cfg.sig_min) closeTf(inc, tf, ag);
          else pending = ag;
        }
        delete inc.agg[tf];
        ag = null;
      }
      if (!ag) inc.agg[tf] = { t: B, o: o, h: h, l: l, c: c, n: 1 };
      else {
        ag.h = Math.max(ag.h, h);
        ag.l = Math.min(ag.l, l);
        ag.c = c;
        ag.n += 1;
      }
      if (close === B + step) {
        var buck = inc.agg[tf];
        delete inc.agg[tf];
        if (keepInc || buck.n === tf) {
          if (tf !== cfg.sig_min) closeTf(inc, tf, buck);
          else pending = buck;
        }
      }
    }
    fills(inc, t, o, h, l, c);
    if (pending) {
      var pair = closeTf(inc, cfg.sig_min, pending);
      var raw = pair[0], allowed = pair[1];
      if (raw && inc.grid && inc.grid.qty === 0) inc.grid = null;
      if (allowed && !inc.grid && pending.t >= W0) {
        inc.grid = { vd: allowed, p: pending.c, qty: 0, cost: 0, nu: 0, lvl: 1, first: null, fees: 0, t0: ms(pending.t) };
      }
    }
    inc.last_t = t;
    inc.last_c = c;
  }

  function dumpState(inc) {
    var g = null;
    if (inc.grid) {
      g = {
        vd: inc.grid.vd | 0, p: inc.grid.p, qty: inc.grid.qty, cost: inc.grid.cost,
        nu: inc.grid.nu | 0, lvl: inc.grid.lvl | 0,
        first: ms(inc.grid.first),
        fees: inc.grid.fees, t0: ms(inc.grid.t0)
      };
    }
    var agg = {};
    Object.keys(inc.agg).forEach(function (k) { agg[String(k)] = inc.agg[k]; });
    return {
      lastMinT: ms(inc.last_t),
      lastPx: inc.last_c == null ? null : +inc.last_c,
      s: inc.s | 0, started: !!inc.started,
      colors: inc.colors.slice(-inc.W).map(function (x) { return x | 0; }),
      W: inc.W | 0,
      lock_hist: inc.lock_hist.slice(-8).map(function (p) { return [ms(p[0]), +p[1]]; }),
      sig: hamaDump(inc.sig),
      cnt: hamaDump(inc.cnt),
      lock_h: inc.lock_h ? hamaDump(inc.lock_h) : null,
      grid: g,
      agg: agg
    };
  }

  function loadInc(cfg, d) {
    var inc = newInc(cfg);
    if (!d) return inc;
    inc.s = d.s | 0;
    inc.started = !!d.started;
    inc.colors = (d.colors || []).map(function (x) { return x | 0; });
    inc.W = d.W || inc.W;
    inc.lock_hist = (d.lock_hist || d.lock_last || []).map(function (p) { return [ms(p[0]), +p[1]]; });
    inc.sig = d.sig ? hamaLoad(d.sig) : inc.sig;
    inc.cnt = d.cnt ? hamaLoad(d.cnt) : inc.cnt;
    inc.lock_h = d.lock_h ? hamaLoad(d.lock_h) : inc.lock_h;
    inc.grid = d.grid ? {
      vd: d.grid.vd | 0, p: +d.grid.p, qty: +d.grid.qty, cost: +d.grid.cost,
      nu: d.grid.nu | 0, lvl: d.grid.lvl | 0,
      first: ms(d.grid.first),
      fees: +d.grid.fees, t0: ms(d.grid.t0)
    } : null;
    inc.agg = {};
    if (d.agg) {
      Object.keys(d.agg).forEach(function (k) {
        var b = d.agg[k];
        inc.agg[+k] = { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, n: b.n };
      });
    }
    inc.last_t = ms(d.lastMinT);
    inc.last_c = d.lastPx == null ? null : +d.lastPx;
    return inc;
  }

  function catchup(state, mins) {
    var cfg = arguments[2] || null;
    // state is dump; we need cfg. Caller passes inc or (dump, mins, cfg).
    return null;
  }

  function runCatchup(inc, mins) {
    var n0 = inc.trades.length;
    var nMin = 0;
    for (var i = 0; i < mins.length; i++) {
      var m = mins[i];
      if (inc.last_t != null && m.t <= inc.last_t) continue;
      onMin(inc, m.t, m.o, m.h, m.l, m.c);
      nMin++;
    }
    return { nMin: nMin, trades: inc.trades.slice(n0), state: dumpState(inc) };
  }

  function markOpen(inc, t, px) {
    var g = inc.grid;
    if (!g || g.qty <= 0) return null;
    var avg = g.cost / g.qty;
    var r = g.vd * (px / avg - 1) * g.qty * inc.cfg.unit;
    var f = (g.fees + g.qty * inc.cfg.fee) * inc.cfg.unit;
    return {
      entry_t: g.first, exit_t: t, dir: g.vd, entry_px: avg, exit_px: px,
      pnl: r, fee: f, net: r - f, fills: g.nu, maxlev: g.nu, why: 0, flip_p: g.p, open: true
    };
  }

  function levels(inc) {
    var g = inc.grid;
    if (!g) return { side: 0, n: 0, avg: null, next: null, stop: null, tp: null, p: null };
    var cfg = inc.cfg;
    var next = (g.nu < cfg.maxu) ? g.p * (1 - g.vd * g.lvl * cfg.step) : null;
    var stop = g.p * (1 - g.vd * (cfg.maxu + 1) * cfg.step);
    var tp = g.p * (1 + g.vd * cfg.tp);
    var avg = g.qty > 0 ? g.cost / g.qty : null;
    return { side: g.vd, n: g.nu, avg: avg, next: next, stop: stop, tp: tp, p: g.p, qty: g.qty };
  }

  function liveShare(inc) { return countShare(inc); }

  return {
    W0: W0, FEE: FEE, UNIT: UNIT, WHY: WHY, CFG: CFG, nBars: nBars,
    newInc: newInc, onMin: onMin, dumpState: dumpState, loadInc: loadInc,
    runCatchup: runCatchup, markOpen: markOpen, levels: levels,
    liveShare: liveShare, biasFromShare: biasFromShare, allowMask: allowMask
  };
});

