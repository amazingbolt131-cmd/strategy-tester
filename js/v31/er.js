// Extreme Reversal indicators + honest grid sim.
// Port of tv_replication/pyengine/scripts/extreme_reversal.py indicators()
// and /workspace/apk39_strats_2y/er_grid_sim/run.py (simulate + helpers).
// No look-ahead. 15m signals on closed candles. 1m fills at order price or gap open.
(function (root, factory) {
  var ER = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = ER;
  root.ER = ER;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var M15 = 900000;
  var TP_PCT = 6.0;
  var FEE_SIDE = 0.0004;
  var HARD_CAP = 50;
  var D_SAME = 0, D_OPP = 1, D_RANDOM = 2;
  var ADD_CONST = 0, ADD_INCR = 1, ADD_THIRDS_RESET = 2, ADD_THIRDS_CARRY = 3;
  var REASON_STOP = 1, REASON_TP = 2, REASON_EOD = 3, REASON_BE = 4;
  var SETTINGS = {
    lenBB: 20, multBB: 0.0, turtleLen: 1, proxPct: 50.0,
    useRSI: false, atrLen: 14, lookbackBars: 1000, targetATRMult: 9.0, tp1: 6.0
  };
  var TICKS = { SOLUSDT: 0.01, BTCUSDT: 0.1, ETHUSDT: 0.01, BNBUSDT: 0.01, XRPUSDT: 0.0001 };
  var R_USD = { ETHUSDT: 10, BNBUSDT: 10, SOLUSDT: 5, BTCUSDT: 5, XRPUSDT: 5 };
  var BEST = { wait: 0, step: 0.20, rev: 0.10, add: ADD_THIRDS_CARRY, exitPct: -1.0, maxu: 5, dir: D_OPP };

  // Per-coin loss-step sizing as tested in apk39_strats_2y/loss_step_sizing/
  // (linear|cap=10|inc=1|reset_win|per_coin). Size is chosen at entry from
  // already-closed trades only; every unit of that trade uses the same size.
  // A trade is a loss if net PnL after 0.04%/side fees is < 0. Dollar risk
  // is the $1–$10 unit multiplied by scale_to_cap so combined closed+open
  // drawdown equals $500 (unit_open_worst 881.7294 → 500/881.7294).
  var LOSS_STEP = { start: 1, inc: 1, cap: 10, scale: 0.567067 };

  function nextLossStep(cur, net, scheme) {
    scheme = scheme || LOSS_STEP;
    if (net > 0) return scheme.start;
    if (net < 0) return Math.min(scheme.cap, cur + scheme.inc);
    return cur;
  }

  // Replay closed trades in entry/close order (no overlap within a coin).
  // Mutates trades, events and position; stores the live counter on result.sizing.
  function applyLossStep(result, scheme) {
    scheme = scheme || LOSS_STEP;
    var trades = result.trades || [];
    var cur = scheme.start;
    var losses = 0;
    var byEntry = {}, byExit = {};
    for (var i = 0; i < trades.length; i++) {
      var tr = trades[i];
      var net = tr.pnl_R - tr.fee_R;
      tr.step = cur;
      tr.R = cur * scheme.scale;
      tr.net_R = net;
      if (net < 0) losses += 1;
      else if (net > 0) losses = 0;
      cur = nextLossStep(cur, net, scheme);
      byEntry[tr.entry_t] = tr;
      byExit[tr.exit_t] = tr;
    }
    var openR = null, openStep = null;
    var events = result.events || [];
    for (var j = 0; j < events.length; j++) {
      var e = events[j];
      if (e.kind === 'ENTRY') {
        var trE = byEntry[e.t];
        if (trE) { openR = trE.R; openStep = trE.step; }
        else { openR = cur * scheme.scale; openStep = cur; }
      }
      e.R = openR;
      e.step = openStep;
      if (e.kind === 'EXIT') {
        var trX = byExit[e.t];
        if (trX) {
          e.R = trX.R;
          e.step = trX.step;
          e.fee = trX.fee_R;
          e.net = trX.net_R;
        }
        openR = null;
        openStep = null;
      }
    }
    var pos = result.position;
    if (pos) {
      pos.step = cur;
      pos.R = cur * scheme.scale;
    }
    result.sizing = {
      nextStep: cur,
      nextR: cur * scheme.scale,
      currentStep: cur,
      currentR: cur * scheme.scale,
      lossesInRow: losses,
      cap: scheme.cap,
      scale: scheme.scale,
      start: scheme.start
    };
    return result.sizing;
  }

  function isN(v) { return v !== v; } // NaN
  function finite(v) { return typeof v === 'number' && isFinite(v); }

  function smaDense(src, len) {
    var n = src.length, out = new Array(n).fill(NaN);
    if (n < len) return out;
    var s = 0;
    for (var i = 0; i < n; i++) {
      s += src[i];
      if (i >= len) s -= src[i - len];
      if (i >= len - 1) out[i] = s / len;
    }
    return out;
  }

  // Pine ta.sma: skip na, then forward-fill on na bars.
  function smaSkipNa(src, len) {
    var n = src.length, out = new Array(n).fill(NaN);
    var buf = [], sum = 0, last = NaN, started = false;
    for (var i = 0; i < n; i++) {
      if (!isN(src[i])) {
        buf.push(src[i]); sum += src[i];
        if (buf.length > len) sum -= buf.shift();
        if (buf.length === len) { last = sum / len; started = true; }
        out[i] = started ? last : NaN;
      } else {
        out[i] = started ? last : NaN;
      }
    }
    return out;
  }

  // ta.rma / ta.atr: SMA seed of first `len` non-na, then alpha=1/len. skipna + ffill.
  function rma(src, len) {
    var n = src.length, out = new Array(n).fill(NaN);
    var compact = [];
    for (var i = 0; i < n; i++) if (!isN(src[i])) compact.push({ i: i, v: src[i] });
    if (compact.length < len) return out;
    var seed = 0;
    for (var k = 0; k < len; k++) seed += compact[k].v;
    var last = seed / len, a = 1 / len;
    out[compact[len - 1].i] = last;
    for (var k2 = len; k2 < compact.length; k2++) {
      last = a * compact[k2].v + (1 - a) * last;
      out[compact[k2].i] = last;
    }
    // forward fill
    var prev = NaN;
    for (var j = 0; j < n; j++) {
      if (!isN(out[j])) prev = out[j];
      else if (!isN(prev)) out[j] = prev;
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
    return rma(tr, len);
  }

  function highest(src, len) {
    var n = src.length, out = new Array(n).fill(NaN);
    for (var i = 0; i < n; i++) {
      if (i < len - 1) continue;
      var mx = -Infinity, ok = false;
      for (var k = i - len + 1; k <= i; k++) {
        if (!isN(src[k])) { if (src[k] > mx) mx = src[k]; ok = true; }
      }
      if (ok) out[i] = mx;
    }
    return out;
  }
  function lowest(src, len) {
    var n = src.length, out = new Array(n).fill(NaN);
    for (var i = 0; i < n; i++) {
      if (i < len - 1) continue;
      var mn = Infinity, ok = false;
      for (var k = i - len + 1; k <= i; k++) {
        if (!isN(src[k])) { if (src[k] < mn) mn = src[k]; ok = true; }
      }
      if (ok) out[i] = mn;
    }
    return out;
  }

  function indicators(bars, p) {
    p = p || SETTINGS;
    var n = bars.length;
    var o = new Array(n), h = new Array(n), l = new Array(n), c = new Array(n), t = new Array(n);
    for (var i = 0; i < n; i++) {
      t[i] = bars[i].t; o[i] = bars[i].o; h[i] = bars[i].h; l[i] = bars[i].l; c[i] = bars[i].c;
    }
    var basis = smaDense(c, p.lenBB);
    var upper = basis.slice(), lower = basis.slice(); // multBB = 0
    if (p.multBB) {
      // not used in these settings
    }
    var hi20 = highest(h, p.turtleLen), lo20 = lowest(l, p.turtleLen);
    var atr = atrTv(h, l, c, p.atrLen);
    var LT = new Array(n).fill(0), ST = new Array(n).fill(0);
    var atrSig = new Array(n).fill(NaN);
    var proxHi = 1 - p.proxPct / 100, proxLo = 1 + p.proxPct / 100;
    for (var j = 0; j < n; j++) {
      var nearLo = !isN(lo20[j]) && c[j] <= lo20[j] * proxLo;
      var nearHi = !isN(hi20[j]) && c[j] >= hi20[j] * proxHi;
      var longT = !isN(lower[j]) && c[j] < lower[j] && nearLo;
      var shortT = !isN(upper[j]) && c[j] > upper[j] && nearHi;
      LT[j] = longT ? 1 : 0;
      ST[j] = shortT ? 1 : 0;
      if (longT || shortT) atrSig[j] = atr[j];
    }
    var avgAtr = smaSkipNa(atrSig, p.lookbackBars);
    var AD = new Array(n);
    var mult = p.targetATRMult / 3.0;
    for (var k = 0; k < n; k++) {
      var base = isN(avgAtr[k]) ? atr[k] : avgAtr[k];
      AD[k] = base * mult;
    }
    var last_sig = new Array(n).fill(0), s = 0;
    for (var q = 0; q < n; q++) {
      if (LT[q]) s = 1;
      else if (ST[q]) s = -1;
      last_sig[q] = s;
    }
    return { t: t, o: o, h: h, l: l, c: c, LT: LT, ST: ST, AD: AD, last_sig: last_sig, atr: atr, basis: basis };
  }

  function floorTick(px, tick) {
    return Math.floor(px / tick + 1e-9) * tick;
  }

  function addQty(addMode, nUnits, tranche) {
    if (addMode <= 0) return { q: 1.0, tranche: tranche };
    if (addMode === 1) return { q: nUnits + 1, tranche: tranche };
    var k = tranche - Math.floor(tranche / 3) * 3;
    var q = k === 0 ? 1 / 3 : (k === 1 ? 2 / 3 : 1.0);
    return { q: q, tranche: tranche + 1 };
  }
  function closeTranche(addMode, tranche) {
    if (addMode === 2) return 0;
    return tranche;
  }
  function trancheFrac(tranche) {
    var k = tranche - Math.floor(tranche / 3) * 3;
    return k === 0 ? 1 / 3 : (k === 1 ? 2 / 3 : 1.0);
  }

  function orderFill(a, b, level, isBuy, isStop, isGap) {
    if (isGap) {
      if (isBuy && isStop && a >= level) return a;
      if (isBuy && !isStop && a <= level) return a;
      if (!isBuy && isStop && a <= level) return a;
      if (!isBuy && !isStop && a >= level) return a;
      return NaN;
    }
    if (isBuy && isStop) {
      if (a < level && b >= level) return level;
      return NaN;
    }
    if (isBuy && !isStop) {
      if (a > level && b <= level) return level;
      return NaN;
    }
    if (!isBuy && isStop) {
      if (a > level && b <= level) return level;
      return NaN;
    }
    if (a < level && b >= level) return level;
    return NaN;
  }

  function resolveDir(dirMode, sig, rng) {
    if (dirMode === 0) return { d: sig, rng: rng };
    if (dirMode === 1) return { d: -sig, rng: rng };
    rng = (rng * 1664525 + 1013904223) >>> 0;
    if (rng < 0x80000000) return { d: 1, rng: rng };
    return { d: -1, rng: rng };
  }

  function arm(px, ad, d, exitPct, useBe, tick) {
    if (!finite(ad) || ad <= 0 || (d !== 1 && d !== -1)) return null;
    var virt = px, atr = ad, extreme = px;
    var hardStop = floorTick(virt - d * atr, tick);
    var hardTp = floorTick(virt * (1 + d * TP_PCT / 100), tick);
    var bePx = useBe ? floorTick(virt * (1 + d * exitPct / 100), tick) : hardTp;
    return { virt: virt, atr: atr, extreme: extreme, hardStop: hardStop, hardTp: hardTp, bePx: bePx };
  }

  function fracLabel(q) {
    if (Math.abs(q - 1 / 3) < 1e-9) return '1/3 R';
    if (Math.abs(q - 2 / 3) < 1e-9) return '2/3 R';
    if (Math.abs(q - 1) < 1e-9) return '1 R';
    return q.toFixed(3) + ' R';
  }

  function simulate(prep, cfg) {
    cfg = cfg || {};
    var o = prep.o, h = prep.h, l = prep.l, c = prep.c, t = prep.t;
    var is_b = prep.is_b, cl15 = prep.cl15, next_sig = prep.next_sig, last_sig = prep.last_sig;
    var LT = prep.LT, ST = prep.ST, AD = prep.AD;
    var win_start = prep.win_start, y2_start = prep.y2_start != null ? prep.y2_start : 1e18;
    var wait_bars = cfg.wait != null ? cfg.wait : BEST.wait;
    var step_pct = cfg.step != null ? cfg.step : BEST.step;
    var rev_pct = cfg.rev != null ? cfg.rev : BEST.rev;
    var add_mode = cfg.add != null ? cfg.add : BEST.add;
    var exit_pct = cfg.exitPct != null ? cfg.exitPct : BEST.exitPct;
    var maxu = cfg.maxu != null ? cfg.maxu : BEST.maxu;
    var dir_mode = cfg.dir != null ? cfg.dir : BEST.dir;
    var tick = prep.tick;
    var rng_state = (cfg.seed != null ? cfg.seed : 1) >>> 0;
    var rec = !!cfg.rec;
    var recEvents = !!cfg.recEvents;
    var forceCloseLast = cfg.forceCloseLast !== false; // default true for parity
    var cap = maxu;
    if (cap <= 0 || cap > HARD_CAP) cap = HARD_CAP;
    var use_be = exit_pct >= 0.0 ? 1 : 0;

    var ntr = 0, n_win = 0, n_be = 0, n_stop = 0, n_tp = 0, n_eod = 0;
    var net_R = 0, fee_tot = 0;
    var y1_R = 0, y1_n = 0, y1_win = 0, y1_fee = 0;
    var y2_R = 0, y2_n = 0, y2_win = 0, y2_fee = 0;
    var closed_eq = 0, peak_closed = 0, max_closed_dd = 0, peak_eq = 0, max_open_dd = 0;
    var max_units = 0, max_qty = 0;

    var state = 0, wait_left = 0, d = 0, virt = 0, atr = 0, next_level = 1, extreme = 0;
    var n_units = 0, qty = 0, avg = 0, entry_t = 0;
    var hard_stop = 0, hard_tp = 0, be_px = 0, pending_d = 0, pending_atr = 0, tranche = 0;
    var trades = rec ? [] : null;
    var events = recEvents ? [] : null;
    var adds = []; // current open adds
    var prevClose = cfg.prevClose;
    var resume = cfg.resume || null;
    if (resume) {
      state = resume.state || 0;
      wait_left = resume.wait_left || 0;
      d = resume.d || 0;
      virt = resume.virt || 0;
      atr = resume.atr || 0;
      next_level = resume.next_level != null ? resume.next_level : 1;
      extreme = resume.extreme || 0;
      n_units = resume.n_units || 0;
      qty = resume.qty || 0;
      avg = resume.avg || 0;
      entry_t = resume.entry_t || 0;
      hard_stop = resume.hard_stop || 0;
      hard_tp = resume.hard_tp || 0;
      be_px = resume.be_px || 0;
      pending_d = resume.pending_d || 0;
      pending_atr = resume.pending_atr || 0;
      tranche = resume.tranche || 0;
      adds = resume.adds ? resume.adds.slice() : [];
      if (resume.rng_state != null) rng_state = resume.rng_state >>> 0;
      if (resume.closed_eq != null) closed_eq = resume.closed_eq;
      if (resume.peak_closed != null) peak_closed = resume.peak_closed;
      if (resume.max_closed_dd != null) max_closed_dd = resume.max_closed_dd;
      if (resume.peak_eq != null) peak_eq = resume.peak_eq;
      if (resume.max_open_dd != null) max_open_dd = resume.max_open_dd;
      if (resume.max_units != null) max_units = resume.max_units;
      if (resume.max_qty != null) max_qty = resume.max_qty;
      if (resume.prevClose != null && prevClose == null) prevClose = resume.prevClose;
    }

    function pushEvent(e) { if (events) events.push(e); }

    function doClose(px, ts, reason) {
      if (qty <= 0 || atr <= 0) {
        n_units = 0; entry_t = 0;
        return;
      }
      var pnl = d * (px - avg) / atr * qty;
      var fee = FEE_SIDE * (avg + px) / atr * qty;
      ntr += 1; net_R += pnl; fee_tot += fee;
      if (pnl > 1e-12) n_win += 1;
      if (reason === REASON_BE) n_be += 1;
      else if (reason === REASON_STOP) n_stop += 1;
      else if (reason === REASON_TP) n_tp += 1;
      else n_eod += 1;
      if (ts < y2_start) {
        y1_R += pnl; y1_n += 1; y1_fee += fee; if (pnl > 1e-12) y1_win += 1;
      } else {
        y2_R += pnl; y2_n += 1; y2_fee += fee; if (pnl > 1e-12) y2_win += 1;
      }
      closed_eq += pnl;
      if (closed_eq > peak_closed) peak_closed = closed_eq;
      var cdd = peak_closed - closed_eq;
      if (cdd > max_closed_dd) max_closed_dd = cdd;
      if (closed_eq > peak_eq) peak_eq = closed_eq;
      var odd = peak_eq - closed_eq;
      if (odd > max_open_dd) max_open_dd = odd;
      if (rec) {
        trades.push({
          entry_t: entry_t, exit_t: ts, entry_px: avg, exit_px: px,
          dir: d, reason: reason, pnl_R: pnl, fee_R: fee,
          qty: qty, nunits: n_units, stop_dist: atr
        });
      }
      pushEvent({
        t: ts, kind: 'EXIT', side: d, price: px, why: reason === REASON_STOP ? 'STOP' : reason === REASON_TP ? 'TP' : reason === REASON_BE ? 'BE' : 'EOD',
        pnl: pnl, qty: qty, nunits: n_units, avg: avg, stop: hard_stop, tp: hard_tp, virt: virt, atr: atr, adds: adds.slice()
      });
      qty = 0; n_units = 0; entry_t = 0; adds = [];
    }

    function updDd(px) {
      var mtm = (qty > 0 && atr > 0) ? d * (px - avg) / atr * qty : 0;
      var eq = closed_eq + mtm;
      if (eq > peak_eq) peak_eq = eq;
      var dd = peak_eq - eq;
      if (dd > max_open_dd) max_open_dd = dd;
    }

    function doAdd(fx, ts, qadd) {
      var new_q = qty + qadd;
      avg = (avg * qty + fx * qadd) / new_q;
      qty = new_q;
      n_units += 1;
      if (entry_t === 0) entry_t = ts;
      if (n_units > max_units) max_units = n_units;
      if (qty > max_qty) max_qty = qty;
      var recAdd = { t: ts, px: fx, q: qadd, label: fracLabel(qadd), n: n_units };
      adds.push(recAdd);
      pushEvent({
        t: ts, kind: n_units === 1 ? 'ENTRY' : 'ADD', side: d, price: fx,
        q: qadd, label: recAdd.label, qty: qty, nunits: n_units, avg: avg,
        stop: hard_stop, tp: hard_tp, virt: virt, atr: atr
      });
    }

    function processSeg(a, b, is_gap, ts) {
      var hard = 0, cur = a, gap = is_gap;
      if (qty > 0) {
        updDd(cur);
        var fx = orderFill(cur, b, hard_stop, d === -1, true, gap);
        if (!isN(fx)) {
          updDd(fx);
          doClose(fx, ts, REASON_STOP);
          avg = 0;
          tranche = closeTranche(add_mode, tranche);
          state = wait_bars <= 0 ? 3 : 1;
          return 1;
        }
        if (use_be === 1) {
          fx = orderFill(cur, b, be_px, d === -1, false, gap);
          if (!isN(fx)) {
            updDd(fx);
            doClose(fx, ts, REASON_BE);
            avg = 0;
            tranche = closeTranche(add_mode, tranche);
            next_level = 1;
            extreme = fx;
            cur = fx;
            gap = false;
          }
        }
        fx = orderFill(cur, b, hard_tp, d === -1, false, gap);
        if (!isN(fx)) {
          if (qty > 0) {
            updDd(fx);
            doClose(fx, ts, REASON_TP);
            avg = 0;
            tranche = closeTranche(add_mode, tranche);
            state = wait_bars <= 0 ? 3 : 1;
            return 1;
          }
        }
      }

      if (state !== 2) return 0;

      if (d === 1) {
        if (cur < extreme) extreme = cur;
        if (b < extreme) extreme = b;
      } else {
        if (cur > extreme) extreme = cur;
        if (b > extreme) extreme = b;
      }

      var safety = 0;
      while (n_units < cap && safety < HARD_CAP + 2) {
        safety += 1;
        var k = next_level;
        var level_px = floorTick(virt * (1.0 - d * (k * step_pct) / 100.0), tick);
        if (rev_pct <= 0.0) {
          var fxA = orderFill(cur, b, level_px, d === 1, false, gap);
          if (isN(fxA)) break;
          var aq = addQty(add_mode, n_units, tranche);
          tranche = aq.tranche;
          doAdd(fxA, ts, aq.q);
          next_level = k + 1;
          cur = fxA;
          gap = false;
          updDd(fxA);
          if (use_be === 1 && qty > 0) {
            var fx2 = orderFill(cur, b, be_px, d === -1, false, false);
            if (!isN(fx2)) {
              updDd(fx2);
              doClose(fx2, ts, REASON_BE);
              avg = 0;
              tranche = closeTranche(add_mode, tranche);
              next_level = 1;
              extreme = fx2;
              cur = fx2;
              break;
            }
          }
          if (qty > 0) {
            fx2 = orderFill(cur, b, hard_stop, d === -1, true, false);
            if (!isN(fx2)) {
              updDd(fx2);
              doClose(fx2, ts, REASON_STOP);
              avg = 0;
              tranche = closeTranche(add_mode, tranche);
              state = wait_bars <= 0 ? 3 : 1;
              hard = 1;
              break;
            }
            fx2 = orderFill(cur, b, hard_tp, d === -1, false, false);
            if (!isN(fx2)) {
              updDd(fx2);
              doClose(fx2, ts, REASON_TP);
              avg = 0;
              tranche = closeTranche(add_mode, tranche);
              state = wait_bars <= 0 ? 3 : 1;
              hard = 1;
              break;
            }
          }
        } else {
          if (d === 1) {
            if (cur < extreme) extreme = cur;
            if (b < extreme) extreme = b;
            var dd = virt > 0 ? (virt - extreme) / virt * 100.0 : 0;
          } else {
            if (cur > extreme) extreme = cur;
            if (b > extreme) extreme = b;
            dd = virt > 0 ? (extreme - virt) / virt * 100.0 : 0;
          }
          var reached = Math.floor(dd / step_pct + 1e-9);
          if (reached < k) break;
          var trig = d === 1
            ? floorTick(extreme * (1.0 + rev_pct / 100.0), tick)
            : floorTick(extreme * (1.0 - rev_pct / 100.0), tick);
          var fxB = orderFill(cur, b, trig, d === 1, true, gap);
          if (isN(fxB)) break;
          var aq2 = addQty(add_mode, n_units, tranche);
          tranche = aq2.tranche;
          doAdd(fxB, ts, aq2.q);
          next_level = reached + 1;
          if (next_level <= k) next_level = k + 1;
          cur = fxB;
          gap = false;
          updDd(fxB);
          if (use_be === 1 && qty > 0) {
            var fx3 = orderFill(cur, b, be_px, d === -1, false, false);
            if (!isN(fx3)) {
              updDd(fx3);
              doClose(fx3, ts, REASON_BE);
              avg = 0;
              tranche = closeTranche(add_mode, tranche);
              next_level = 1;
              extreme = fx3;
              cur = fx3;
              break;
            }
          }
          if (qty > 0) {
            fx3 = orderFill(cur, b, hard_stop, d === -1, true, false);
            if (!isN(fx3)) {
              updDd(fx3);
              doClose(fx3, ts, REASON_STOP);
              avg = 0;
              tranche = closeTranche(add_mode, tranche);
              state = wait_bars <= 0 ? 3 : 1;
              hard = 1;
              break;
            }
            fx3 = orderFill(cur, b, hard_tp, d === -1, false, false);
            if (!isN(fx3)) {
              updDd(fx3);
              doClose(fx3, ts, REASON_TP);
              avg = 0;
              tranche = closeTranche(add_mode, tranche);
              state = wait_bars <= 0 ? 3 : 1;
              hard = 1;
              break;
            }
          }
        }
      }

      if (qty > 0 && hard === 0) updDd(b);
      else if (state === 2 && qty === 0 && hard === 0) {
        var fxV = orderFill(a, b, hard_stop, d === -1, true, is_gap);
        if (!isN(fxV)) state = 0;
        else {
          fxV = orderFill(a, b, hard_tp, d === -1, false, is_gap);
          if (!isN(fxV)) state = 0;
        }
      }
      return hard;
    }

    function applyArm(px, ad, nd) {
      var a = arm(px, ad, nd, exit_pct, use_be, tick);
      if (a) {
        d = nd;
        virt = a.virt; atr = a.atr; extreme = a.extreme;
        hard_stop = a.hardStop; hard_tp = a.hardTp; be_px = a.bePx;
        state = 2; next_level = 1; n_units = 0; qty = 0; avg = 0; entry_t = 0; adds = [];
        return true;
      }
      state = 0;
      return false;
    }

    var n = t.length, i = 0;
    while (i < n) {
      var ts = t[i];
      if (ts < win_start) { i += 1; continue; }
      if (state === 0 && qty === 0) {
        var j = next_sig[i];
        if (j >= n) break;
        i = j;
        ts = t[i];
        if (ts < win_start) { i += 1; continue; }
      }

      var cl = cl15[i], oi = o[i], hi = h[i], lo = l[i], ci = c[i];

      if (state === 3 && qty === 0) {
        var sig = 0;
        if (cl >= 0) sig = last_sig[cl];
        if (sig === 0) sig = d !== 0 ? d : 1;
        var rd = resolveDir(dir_mode, sig, rng_state); pending_d = rd.d; rng_state = rd.rng;
        var ad0 = cl >= 0 ? AD[cl] : pending_atr;
        applyArm(i === 0 ? (prevClose != null ? prevClose : ci) : c[i - 1], ad0, pending_d);
      }

      if (is_b[i] && cl >= 0 && (LT[cl] || ST[cl]) && qty === 0) {
        sig = LT[cl] ? 1 : -1;
        rd = resolveDir(dir_mode, sig, rng_state); rng_state = rd.rng;
        var ad = AD[cl];
        if (finite(ad) && ad > 0) {
          pending_d = rd.d;
          pending_atr = ad;
          if (wait_bars <= 0) {
            if (applyArm(i > 0 ? c[i - 1] : (prevClose != null ? prevClose : oi), ad, rd.d)) tranche = 0;
          } else {
            d = rd.d;
            state = 1;
            wait_left = wait_bars;
            tranche = 0;
          }
        }
      }

      if (state === 1) {
        wait_left -= 1;
        if (wait_left <= 0) {
          ad = pending_atr;
          if (!finite(ad) || ad <= 0) ad = cl >= 0 ? AD[cl] : 0;
          if (applyArm(ci, ad, pending_d)) { i += 1; continue; }
          state = 0;
        }
        i += 1;
        continue;
      }

      if (state === 2) {
        if (qty === 0 && !is_b[i]) {
          var interesting = 0;
          if (d === 1) {
            var arm_px = virt * (1.0 - next_level * step_pct / 100.0);
            if (lo <= arm_px || lo <= hard_stop || hi >= hard_tp) interesting = 1;
            else if (rev_pct > 0 && virt > 0) {
              if ((virt - extreme) / virt * 100.0 + 1e-12 >= next_level * step_pct) {
                var trigS = extreme * (1.0 + rev_pct / 100.0);
                if (hi >= trigS || lo < extreme) interesting = 1;
              }
            }
          } else {
            arm_px = virt * (1.0 + next_level * step_pct / 100.0);
            if (hi >= arm_px || hi >= hard_stop || lo <= hard_tp) interesting = 1;
            else if (rev_pct > 0 && virt > 0) {
              if ((extreme - virt) / virt * 100.0 + 1e-12 >= next_level * step_pct) {
                trigS = extreme * (1.0 - rev_pct / 100.0);
                if (lo <= trigS || hi > extreme) interesting = 1;
              }
            }
          }
          if (interesting === 0) { i += 1; continue; }
        }

        var p1, p2;
        if ((hi - oi) < (oi - lo)) { p1 = hi; p2 = lo; }
        else { p1 = lo; p2 = hi; }
        var segs = [[oi, oi, true], [oi, p1, false], [p1, p2, false], [p2, ci, false]];
        for (var s = 0; s < 4; s++) {
          var hard = processSeg(segs[s][0], segs[s][1], segs[s][2], ts);
          if (hard === 1) {
            if (state === 1) wait_left = wait_bars;
            break;
          }
        }
      }

      i += 1;
    }

    if (forceCloseLast && qty > 0) {
      doClose(c[n - 1], t[n - 1], REASON_EOD);
    }

    var stateName = 'flat';
    if (qty > 0) stateName = 'open';
    else if (state === 2) stateName = 'virtual';
    else if (state === 1 || state === 3) stateName = 'virtual';

    return {
      ntr: ntr, n_win: n_win, n_be: n_be, n_stop: n_stop, n_tp: n_tp, n_eod: n_eod,
      net_R: net_R, fee_tot: fee_tot,
      y1_R: y1_R, y1_n: y1_n, y1_win: y1_win, y1_fee: y1_fee,
      y2_R: y2_R, y2_n: y2_n, y2_win: y2_win, y2_fee: y2_fee,
      max_closed_dd: max_closed_dd, max_open_dd: max_open_dd, max_units: max_units, max_qty: max_qty,
      rng_state: rng_state,
      trades: trades || [],
      events: events || [],
      position: {
        stateName: stateName,
        state: state,
        side: d,
        virt: virt,
        avg: qty > 0 ? avg : 0,
        stop: hard_stop,
        tp: hard_tp,
        atr: atr,
        qty: qty,
        n_units: n_units,
        tranche: tranche,
        nextFrac: trancheFrac(tranche),
        nextLevel: next_level,
        extreme: extreme,
        entry_t: entry_t,
        adds: adds,
        wait_left: wait_left,
        be_px: be_px,
        pending_d: pending_d,
        pending_atr: pending_atr
      },
      lastBarT: n ? t[n - 1] : (resume && resume.prevBarT != null ? resume.prevBarT : null),
      engine: {
        state: state, wait_left: wait_left, d: d, virt: virt, atr: atr,
        next_level: next_level, extreme: extreme, n_units: n_units, qty: qty,
        avg: avg, entry_t: entry_t, hard_stop: hard_stop, hard_tp: hard_tp,
        be_px: be_px, pending_d: pending_d, pending_atr: pending_atr,
        tranche: tranche, adds: adds.slice(), rng_state: rng_state,
        closed_eq: closed_eq, peak_closed: peak_closed, max_closed_dd: max_closed_dd,
        peak_eq: peak_eq, max_open_dd: max_open_dd, max_units: max_units, max_qty: max_qty,
        prevClose: n ? c[n - 1] : (prevClose != null ? prevClose : null),
        prevBarT: n ? t[n - 1] : (resume && resume.prevBarT != null ? resume.prevBarT : null)
      }
    };
  }

  function buildPrep(bars1, ind, tick, win_start, win_end) {
    // bars1: [{t,o,h,l,c}], closed 1m. ind from 15m indicators().
    var t15 = ind.t, n15 = t15.length;
    var t = [], o = [], h = [], l = [], c = [];
    var loT = win_start - 4 * M15, hiT = win_end != null ? win_end : Infinity;
    for (var i = 0; i < bars1.length; i++) {
      var ts = bars1[i].t;
      if (ts >= loT && ts < hiT) {
        t.push(ts); o.push(bars1[i].o); h.push(bars1[i].h); l.push(bars1[i].l); c.push(bars1[i].c);
      }
    }
    var n = t.length;
    var is_b = new Array(n), cl15 = new Array(n);
    // cl15 = searchsorted(t15+M15, t, side=right)-1
    var close15 = new Array(n15);
    for (var j = 0; j < n15; j++) close15[j] = t15[j] + M15;
    var p = 0;
    for (var k = 0; k < n; k++) {
      var x = t[k];
      while (p < n15 && close15[p] <= x) p++;
      // p is first close15 > x? wait: side=right means first index with close15 > x? 
      // numpy searchsorted side=right: first index where close15 > x? 
      // Actually side='right' returns insertion point to the right of any equal values,
      // i.e. first index with close15 > x (if all unique). If close15[j]==x, insertion is after it.
      // We want last j with close15[j] <= x, which is searchsorted(..., side='right')-1. Yes.
      // But my while advances p while close15[p] <= x, so p is first with close15 > x, cl=p-1. Good.
      // However p only moves forward, requiring t sorted. Yes.
      cl15[k] = p - 1;
      is_b[k] = (t[k] % M15 === 0) ? 1 : 0;
    }
    var sig = new Array(n).fill(0);
    for (var i2 = 0; i2 < n; i2++) {
      if (is_b[i2] && t[i2] >= win_start) {
        var cl = cl15[i2];
        if (cl >= 0 && (ind.LT[cl] || ind.ST[cl])) sig[i2] = 1;
      }
    }
    var next_sig = new Array(n), nxt = n;
    for (var i3 = n - 1; i3 >= 0; i3--) {
      if (sig[i3]) nxt = i3;
      next_sig[i3] = nxt;
    }
    return {
      tick: tick, t: t, o: o, h: h, l: l, c: c,
      is_b: is_b, cl15: cl15, next_sig: next_sig,
      LT: ind.LT, ST: ind.ST, AD: ind.AD, last_sig: ind.last_sig,
      win_start: win_start, y2_start: 1e18
    };
  }

  // Helpers to pack from raw arrays (parity / unit tests)
  function packSynthetic(o, h, l, c, t, is_b, cl15, LT, ST, AD, last_sig, tick, win_start, y2_start) {
    var n = t.length, sig = new Array(n).fill(0);
    for (var i = 0; i < n; i++) {
      if (is_b[i] && cl15[i] >= 0 && (LT[cl15[i]] || ST[cl15[i]])) sig[i] = 1;
    }
    var next_sig = new Array(n), nxt = n;
    for (var i2 = n - 1; i2 >= 0; i2--) {
      if (sig[i2]) nxt = i2;
      next_sig[i2] = nxt;
    }
    return {
      tick: tick, t: t, o: o, h: h, l: l, c: c,
      is_b: is_b, cl15: cl15, next_sig: next_sig,
      LT: LT, ST: ST, AD: AD, last_sig: last_sig,
      win_start: win_start, y2_start: y2_start != null ? y2_start : 1e18
    };
  }


  function snapshotIndicators(bars, ind, p) {
    p = p || SETTINGS;
    var n = bars.length;
    if (!n) return null;
    var buf = [];
    for (var i = 0; i < n; i++) {
      if (ind.LT[i] || ind.ST[i]) {
        var av = ind.atr[i];
        if (!isN(av)) buf.push(av);
      }
    }
    var started = buf.length >= p.lookbackBars;
    if (buf.length > p.lookbackBars) buf = buf.slice(buf.length - p.lookbackBars);
    var closes = [];
    var from = Math.max(0, n - p.lenBB);
    for (var j = from; j < n; j++) closes.push(bars[j].c);
    // Capture smaDense's running sum (including FP drift over the full series)
    // so continueIndicators stays bit-identical to a full indicators() run.
    var smaSum = 0, lenBB = p.lenBB;
    for (var i2 = 0; i2 < n; i2++) {
      smaSum += bars[i2].c;
      if (i2 >= lenBB) smaSum -= bars[i2 - lenBB].c;
    }
    var b = bars[n - 1];
    return {
      t: b.t, o: b.o, h: b.h, l: b.l, c: b.c,
      atrRma: ind.atr[n - 1],
      atrLen: p.atrLen,
      atrSigBuf: buf.slice(),
      atrSigStarted: started,
      lookbackBars: p.lookbackBars,
      last_sig: ind.last_sig[n - 1],
      closes: closes,
      smaSum: smaSum,
      LT: ind.LT[n - 1],
      ST: ind.ST[n - 1],
      AD: ind.AD[n - 1],
      atr: ind.atr[n - 1]
    };
  }

  function continueIndicators(snap, newBars, p) {
    p = p || SETTINGS;
    if (!snap) return { bars: newBars.slice(), ind: indicators(newBars, p), snap: null };
    var bars = [{ t: snap.t, o: snap.o, h: snap.h, l: snap.l, c: snap.c }];
    for (var i = 0; i < newBars.length; i++) {
      if (newBars[i].t > snap.t) bars.push(newBars[i]);
    }
    var n = bars.length;
    var o = new Array(n), h = new Array(n), l = new Array(n), c = new Array(n), t = new Array(n);
    for (var i2 = 0; i2 < n; i2++) {
      t[i2] = bars[i2].t; o[i2] = bars[i2].o; h[i2] = bars[i2].h;
      l[i2] = bars[i2].l; c[i2] = bars[i2].c;
    }
    var LT = new Array(n).fill(0), ST = new Array(n).fill(0);
    var AD = new Array(n), atr = new Array(n).fill(NaN), last_sig = new Array(n).fill(0);
    LT[0] = snap.LT ? 1 : 0;
    ST[0] = snap.ST ? 1 : 0;
    AD[0] = snap.AD;
    atr[0] = snap.atrRma;
    last_sig[0] = snap.last_sig || 0;

    var buf = (snap.atrSigBuf || []).slice();
    var started = !!snap.atrSigStarted || buf.length >= p.lookbackBars;
    var lastRma = snap.atrRma;
    var prevC = snap.c;
    var closes = (snap.closes || []).slice();
    var smaSum = snap.smaSum != null ? snap.smaSum : closes.reduce(function (a, v) { return a + v; }, 0);
    var s = snap.last_sig || 0;
    var a = 1 / p.atrLen;
    var mult = p.targetATRMult / 3.0;
    var proxHi = 1 - p.proxPct / 100, proxLo = 1 + p.proxPct / 100;

    function meanBuf(b) {
      if (!b.length) return NaN;
      var sm = 0;
      for (var k = 0; k < b.length; k++) sm += b[k];
      return sm / b.length;
    }

    for (var j = 1; j < n; j++) {
      var hl = h[j] - l[j], hc = Math.abs(h[j] - prevC), lc = Math.abs(l[j] - prevC);
      var tr = hl > hc ? hl : hc; if (lc > tr) tr = lc;
      lastRma = a * tr + (1 - a) * lastRma;
      atr[j] = lastRma;
      prevC = c[j];
      if (closes.length >= p.lenBB) {
        smaSum += c[j];
        smaSum -= closes[0];
        closes.push(c[j]);
        closes.shift();
      } else {
        smaSum += c[j];
        closes.push(c[j]);
      }
      var basis = closes.length >= p.lenBB ? smaSum / p.lenBB : NaN;
      var lower = basis, upper = basis;
      var nearLo = c[j] <= l[j] * proxLo;
      var nearHi = c[j] >= h[j] * proxHi;
      var longT = !isN(lower) && c[j] < lower && nearLo;
      var shortT = !isN(upper) && c[j] > upper && nearHi;
      LT[j] = longT ? 1 : 0;
      ST[j] = shortT ? 1 : 0;
      if (longT || shortT) {
        buf.push(atr[j]);
        if (buf.length >= p.lookbackBars) started = true;
        if (buf.length > p.lookbackBars) buf.shift();
      }
      var base = started ? meanBuf(buf) : atr[j];
      AD[j] = base * mult;
      if (longT) s = 1;
      else if (shortT) s = -1;
      last_sig[j] = s;
    }

    var newSnap = {
      t: t[n - 1], o: o[n - 1], h: h[n - 1], l: l[n - 1], c: c[n - 1],
      atrRma: atr[n - 1],
      atrLen: p.atrLen,
      atrSigBuf: buf.slice(),
      atrSigStarted: started,
      lookbackBars: p.lookbackBars,
      last_sig: last_sig[n - 1],
      closes: closes.slice(),
      smaSum: smaSum,
      LT: LT[n - 1],
      ST: ST[n - 1],
      AD: AD[n - 1],
      atr: atr[n - 1]
    };
    return {
      bars: bars,
      ind: { t: t, o: o, h: h, l: l, c: c, LT: LT, ST: ST, AD: AD, last_sig: last_sig, atr: atr },
      snap: newSnap
    };
  }

  return {
    SETTINGS: SETTINGS, TICKS: TICKS, R_USD: R_USD, BEST: BEST,
    M15: M15, TP_PCT: TP_PCT, FEE_SIDE: FEE_SIDE, HARD_CAP: HARD_CAP,
    D_SAME: D_SAME, D_OPP: D_OPP, D_RANDOM: D_RANDOM,
    ADD_CONST: ADD_CONST, ADD_INCR: ADD_INCR,
    ADD_THIRDS_RESET: ADD_THIRDS_RESET, ADD_THIRDS_CARRY: ADD_THIRDS_CARRY,
    REASON_STOP: REASON_STOP, REASON_TP: REASON_TP, REASON_EOD: REASON_EOD, REASON_BE: REASON_BE,
    indicators: indicators, simulate: simulate, buildPrep: buildPrep,
    packSynthetic: packSynthetic, floorTick: floorTick, smaDense: smaDense,
    smaSkipNa: smaSkipNa, atrTv: atrTv, fracLabel: fracLabel, trancheFrac: trancheFrac,
    LOSS_STEP: LOSS_STEP, nextLossStep: nextLossStep, applyLossStep: applyLossStep,
    snapshotIndicators: snapshotIndicators, continueIndicators: continueIndicators
  };
});
