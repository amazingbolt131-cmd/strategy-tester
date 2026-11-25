/* Run one v3.1 strategy on candles passed as JSON on stdin.
   The engines in this folder are the signal math from Strategy Index v3.1.
   Nothing here talks to a hosted database, a webhook, or a broker. */
"use strict";

const fs = require("fs");
const HAMA = require("./hama.js");
const ER = require("./er.js");
const S5 = require("./s5.js");
const DG = require("./dg.js");
const HG = require("./hg.js");
const COMBO = require("./combo.js");
const SOL1M = require("./sol1m.js");

const FEE = 0.0004;
const WEIGHTS = COMBO.WEIGHTS;
const UNIT = COMBO.UNIT;
const ER_SCALE = 0.36023713902975735;
const S5_BASE = { pivex: 111.41632457034615, binance: 22.085622688828757 };
const HAMA_UNIT = { hama_30m: 20.739227210345963, hama_1h: 7.592660371373892 };
const HAMA_STEPS = { hama_30m: 1, hama_1h: 8 };
const HAMA_KEY = { hama_30m: "HAMA30", hama_1h: "HAMA1h" };

function bars(rows, ms) {
  if (!rows) return [];
  return rows.map(function (r) {
    return { t: r[0], o: r[1], h: r[2], l: r[3], c: r[4], ct: r[0] + ms };
  });
}

function colourSeries(dailyRows) {
  if (!dailyRows || !dailyRows.length) return null;
  return COMBO.dailyColourSeries(bars(dailyRows, 86400000));
}

function colourOf(series, ms) {
  if (!series) return 0;
  return COMBO.colourAt(series, ms);
}

function scaleOf(id, venue, k) {
  if (venue !== "pivex") return 1;
  const key = {
    hama_30m: "HAMA30", hama_1h: "HAMA1h", s5_15m: "S5",
    micro_1h: "Micro", turtle_5m: "Turtle", ml_5m: "ML"
  }[id];
  if (!key) return 1;
  return WEIGHTS[key] * k;
}

function row(t) {
  return {
    side: t.side, qty: t.qty, entry_ms: t.entry_ms, exit_ms: t.exit_ms,
    entry: t.entry, exit: t.exit, stop: t.stop == null ? null : t.stop, tp: t.tp == null ? null : t.tp,
    entry_reason: t.entry_reason, exit_reason: t.exit_reason,
    gross: t.gross, fee: t.fee, net: t.net,
    run_up: t.run_up || 0, drawdown: t.drawdown || 0,
    mult: t.mult == null ? 1 : t.mult, tranche: t.tranche == null ? null : t.tranche,
    adjustable: t.adjustable !== false
  };
}

function keep(t, start, end) {
  // Warmup builds indicators only. A fill from before the window is not a test trade.
  return t.entry_ms >= start && t.entry_ms < end;
}

function hama(req) {
  const ms = req.series.chart_ms;
  const chart = bars(req.series.chart, ms);
  const htf = req.series.htf ? bars(req.series.htf, req.series.htf_ms) : null;
  const h1 = bars(req.series.h1, req.series.h1_ms || 3600000);
  const unit = HAMA_UNIT[req.id];
  const steps = HAMA_STEPS[req.id];
  const scale = scaleOf(req.id, req.venue, req.k);
  const R = unit * steps * scale;
  const series = req.daily_filter && req.venue === "pivex" ? colourSeries(req.series.daily) : null;
  const sol = req.series.sol || [];
  const result = HAMA.run(
    { gap: 0.3, R: R, steps: steps },
    { chart: chart, htf: htf, h1: h1 },
    {
      startMs: req.start, endMs: req.end, forceCloseLast: true,
      minStopAt: req.symbol === "SOLUSDT" ? null : function (ct, entry) {
        let close = 0;
        for (let i = 0; i < sol.length; i++) if (sol[i][0] <= ct) close = sol[i][1]; else break;
        return close > 0 ? entry * 0.25 / close : 0;
      },
      allow: series ? function (ct, side) { return colourOf(series, ct) === side; } : null
    }
  );
  const trades = result.trades.map(function (t) {
    const gross = t.pnl;
    const fee = FEE * t.units * (t.entry + t.exit);
    return row({
      side: t.side, qty: t.units, entry_ms: t.entryMs, exit_ms: t.exitMs,
      entry: t.entry, exit: t.exit, stop: t.stop, tp: null,
      entry_reason: "HAMA flip", exit_reason: t.why,
      gross: gross, fee: fee, net: gross - fee, mult: t.risk / (unit * scale)
    });
  });
  return {
    trades: trades,
    meta: {
      scale: scale, unit: unit * scale, steps: steps,
      note: (req.venue === "pivex"
        ? "Pivex single entry. HAMA risk $" + (unit * scale).toFixed(2) + " at step 1 (unit × weight × k=" + req.k + ")."
        : "Binance single entry. HAMA risk $" + unit.toFixed(2) + " at step 1, not scaled by k. This strategy has no add.")
    }
  };
}

function emptyS5() {
  return {
    pos: null, pend: null, pendAdd: null, half: null, last15T: -1,
    T: {
      sess: null, sH: null, sL: null, sC: null, pC: null, satr: null, seed: 0, cnt: 0, skip: true,
      eH: [], eL: [], xH: [], xL: [], side: 0, fstop: null, stop: null, hist: []
    },
    I: { prevClose: null, atr: null, closes: [], highs: [], lows: [], trig: [] }
  };
}

function s5(req) {
  const m15 = bars(req.series.m15, 900000);
  const m1 = bars(req.series.m1, 60000);
  const st = emptyS5();
  const baskets = [];
  const series = req.daily_filter && req.venue === "pivex" ? colourSeries(req.series.daily) : null;
  let k = 0;
  let basket = null;
  for (let i = 0; i < m15.length; i++) {
    const b = m15[i];
    const mins = [];
    const lim = b.t + 900000;
    while (k < m1.length && m1[k].t < lim) {
      if (m1[k].t >= b.t) mins.push(m1[k]);
      k++;
    }
    const pendBefore = st.pend;
    // Pivex takes one entry. Drop an add armed on the previous close before this bar can fill it.
    if (req.venue === "pivex") st.pendAdd = null;
    const r = S5.step15(st, b, mins);
    if (req.venue === "pivex") st.pendAdd = null;
    if (series && st.pend && st.pend !== pendBefore && colourOf(series, b.t + 900000) !== st.pend.dir) st.pend = null;
    // Drop anything opened before the window so the month starts flat. Indicators stay warm.
    if (lim <= req.start) {
      st.pos = null; st.pend = null; st.pendAdd = null; basket = null;
      continue;
    }
    r.events.forEach(function (e) {
      if (e.kind === "ENTRY") basket = { dir: e.dir, e0: e.price, q0: e.q, t0: e.t, stop: e.stop, tp: e.tp, q1: 0 };
      else if (e.kind === "ADD" && basket) { basket.e1 = e.price; basket.q1 = e.q; basket.t1 = e.t; }
      else if (e.kind === "TP" && basket && e.tranche === 1) basket.tp1 = { t: e.t, px: e.price };
      else if (e.kind === "TP" && basket && e.tranche === 2) basket.tp2 = { t: e.t, px: e.price };
      else if (e.kind === "EXIT" && basket) {
        basket.exit_t = e.t; basket.exit_px = e.price; basket.why = e.why; basket.net_R = e.net_R;
        baskets.push(basket); basket = null;
      }
    });
  }
  if (st.pos && m1.length) {
    const last = m1[m1.length - 1];
    const p = st.pos;
    if (p.op0) { p.pnl += p.dir * p.q0 * (last.c - p.e0); p.fee += S5.FEE * p.q0 * last.c; p.op0 = false; p.lastPx = last.c; }
    if (p.op1) { p.pnl += p.dir * p.q1 * (last.c - p.e1); p.fee += S5.FEE * p.q1 * last.c; p.op1 = false; p.lastPx = last.c; }
    baskets.push({
      dir: p.dir, e0: p.e0, q0: p.q0, t0: p.entT, e1: p.e1, q1: p.op1 || p.addUsed ? p.q1 : (p.q1 || 0),
      t1: p.adT, exit_t: last.t + 60000, exit_px: last.c, why: "END", net_R: p.pnl - p.fee,
      tp1: basket && basket.tp1, tp2: basket && basket.tp2, stop: p.stp
    });
  }
  const rule = req.venue === "binance"
    ? { N: 1, X: 1.0, cap: 5, base: S5_BASE.binance }
    : { N: 2, X: 1.0, cap: 2, base: S5_BASE.pivex * scaleOf("s5_15m", req.venue, req.k) };
  const sized = S5.sizingFrom(baskets.map(function (b) { return { net_R: b.net_R }; }), rule);
  const trades = [];
  baskets.forEach(function (b, i) {
    if (b.t0 < req.start || b.t0 >= req.end) return;
    const risk = sized.perTrade[i].risk;
    const mult = sized.perTrade[i].m;
    function leg(entry, entryMs, q, exitPx, exitMs, why, tranche, stop, tp) {
      const grossR = b.dir * q * (exitPx - entry);
      const feeR = S5.FEE * q * (entry + exitPx);
      trades.push(row({
        side: b.dir, qty: q * risk, entry_ms: entryMs, exit_ms: exitMs,
        entry: entry, exit: exitPx, stop: stop, tp: tp,
        entry_reason: tranche === 2 ? "add" : "signal", exit_reason: why,
        gross: grossR * risk, fee: feeR * risk, net: (grossR - feeR) * risk,
        mult: mult, tranche: tranche,
        drawdown: b.mae_R && b.mae_R < 0 ? -b.mae_R * risk : 0
      }));
    }
    const x0 = b.tp1 || { t: b.exit_t, px: b.exit_px };
    const why0 = b.tp1 ? "TP1" : b.why;
    leg(b.e0, b.t0, b.q0, x0.px, x0.t, why0, 1, b.stop, b.tp);
    if (b.q1) {
      const x1 = b.tp2 || { t: b.exit_t, px: b.exit_px };
      const why1 = b.tp2 ? "TP2" : b.why;
      leg(b.e1, b.t1, b.q1, x1.px, x1.t, why1, 2, b.stop, null);
    }
  });
  return {
    trades: trades,
    meta: {
      scale: req.venue === "pivex" ? scaleOf("s5_15m", req.venue, req.k) : 1,
      base: rule.base, cap: rule.cap, n: rule.N,
      note: (req.venue === "pivex"
        ? "Pivex single entry. Base risk $" + rule.base.toFixed(2) + " (unit × weight × k=" + req.k + "), 2× after 2 losses, cap 2×. No add. The 6% target closes that one entry."
        : "Binance multi-entry. Base risk $" + rule.base.toFixed(2) + ", doubles after each loss, cap 5×. The +3% add is a second entry. TP1 closes only the first tranche.")
    }
  };
}

function er(req) {
  if (req.venue === "pivex") throw new Error("ER grid is Binance-only");
  const m15 = bars(req.series.m15, 900000);
  const m1 = bars(req.series.m1, 60000);
  const ind = ER.indicators(m15, ER.SETTINGS);
  const tick = (ER.TICKS && ER.TICKS[req.symbol]) || 0.01;
  const prep = ER.buildPrep(m1, ind, tick, req.start, req.end);
  const result = ER.simulate(prep, {
    rec: true, recEvents: false, forceCloseLast: true,
    wait: ER.BEST.wait, step: ER.BEST.step, rev: ER.BEST.rev,
    add: ER.BEST.add, exitPct: ER.BEST.exitPct, maxu: ER.BEST.maxu, dir: ER.BEST.dir
  });
  ER.applyLossStep(result, { start: 1, inc: 1, cap: 10, scale: ER_SCALE });
  const why = { 1: "STOP", 2: "TP", 3: "END", 4: "BE" };
  const trades = (result.trades || []).filter(function (t) { return keep({ entry_ms: t.entry_t, exit_ms: t.exit_t }, req.start, req.end); }).map(function (t) {
    const dollars = t.R;
    const qty = t.stop_dist > 0 ? t.qty * dollars / t.stop_dist : 0;
    return row({
      side: t.dir, qty: qty, entry_ms: t.entry_t, exit_ms: t.exit_t,
      entry: t.entry_px, exit: t.exit_px, stop: null, tp: null,
      entry_reason: "extreme reversal", exit_reason: why[t.reason] || String(t.reason),
      gross: t.pnl_R * dollars, fee: t.fee_R * dollars, net: t.net_R * dollars,
      mult: t.step
    });
  });
  return { trades: trades, meta: { note: "Binance ER loss-step $0.360 per unit, steps 1 to 10. Thirds-carry adds, 6% target, 0.20% step." } };
}

function dg(req) {
  if (req.venue === "pivex") throw new Error("DG grid is Binance-only");
  const hours = bars(req.series.h1, 3600000);
  const mins = bars(req.series.m1, 60000);
  const st = DG.newState();
  const warm = mins.filter(function (m) { return m.t + 60000 <= req.start; });
  const live = mins.filter(function (m) { return m.t + 60000 > req.start; });
  if (warm.length) DG.catchup(st, hours, warm);
  st.g = { ent: [], qty: [], side: 0, ref: 0, lastAdd: 0, extreme: 0, armed: 0, pend: 0, pendX: false,
           realized: 0, fee: 0, fills: 0, maxlev: 0, t0: null, e0: null };
  const out = DG.catchup(st, hours, live.length ? live : []);
  const closed = out.trades.slice();
  if (mins.length) {
    const last = mins[mins.length - 1];
    const mark = DG.markOpen(st, last.t + 60000, last.c);
    if (mark) closed.push(mark);
  }
  const base = 1000;
  const trades = closed.filter(function (t) { return keep({ entry_ms: t.entry_t, exit_ms: t.exit_t }, req.start, req.end); }).map(function (t) {
    const qty = t.entry_px ? t.fills * base / t.entry_px : 0;
    return row({
      side: t.dir, qty: qty, entry_ms: t.entry_t, exit_ms: t.exit_t,
      entry: t.entry_px, exit: t.exit_px, stop: null, tp: null,
      entry_reason: "grid", exit_reason: DG.WHY[t.why] || String(t.why),
      gross: t.pnl, fee: t.fee, net: t.net, mult: 1
    });
  });
  return { trades: trades, meta: { note: "Binance DG, flat $1,000 a level, 8 levels, no loss step. An unload closes only the newest level." } };
}

function hg(req) {
  if (req.venue === "pivex") throw new Error("HAMA Grid+Count is Binance-only");
  const id = req.id === "hama_grid_1m" ? "g1m" : "g5m";
  const inc = HG.newInc(HG.CFG[id]);
  const mins = bars(req.series.m1, 60000);
  const warm = mins.filter(function (m) { return m.t + 60000 <= req.start; });
  const live = mins.filter(function (m) { return m.t + 60000 > req.start; });
  if (warm.length) HG.runCatchup(inc, warm);
  inc.grid = null;
  inc.trades = [];
  HG.runCatchup(inc, live);
  const closed = inc.trades.slice();
  if (mins.length) {
    const last = mins[mins.length - 1];
    const mark = HG.markOpen(inc, last.t + 60000, last.c);
    if (mark) closed.push(mark);
  }
  const unit = HG.CFG[id].unit;
  const trades = closed.filter(function (t) { return keep({ entry_ms: t.entry_t, exit_ms: t.exit_t }, req.start, req.end); }).map(function (t) {
    const qty = t.entry_px ? t.fills * unit / t.entry_px : 0;
    return row({
      side: t.dir, qty: qty, entry_ms: t.entry_t, exit_ms: t.exit_t,
      entry: t.entry_px, exit: t.exit_px, stop: null, tp: null,
      entry_reason: "HAMA grid", exit_reason: HG.WHY[t.why] || (t.why === 0 ? "END" : String(t.why)),
      gross: t.pnl, fee: t.fee, net: t.net, mult: 1
    });
  });
  const cfg = HG.CFG[id];
  return {
    trades: trades,
    meta: { note: "Binance " + cfg.name + ". $" + unit + " notional per add, step " + (cfg.step * 100) + "%, stop " + (cfg.stop * 100) + "%, target " + (cfg.tp * 100) + "%." }
  };
}

function oneAtATime(trades) {
  const sorted = trades.slice().sort(function (a, b) { return a.entry_ms - b.entry_ms; });
  const kept = [];
  let busy = 0;
  sorted.forEach(function (t) {
    if (t.entry_ms < busy) return;
    kept.push(t);
    busy = t.exit_ms;
  });
  return kept;
}

function micro(req) {
  const h1 = COMBO.barsOf(bars(req.series.h1, 3600000));
  const b5 = COMBO.barsOf(bars(req.series.m5, 300000));
  const btc = COMBO.barsOf(bars(req.series.btc_daily, 86400000));
  const scale = scaleOf("micro_1h", req.venue, req.k);
  const series = req.daily_filter ? colourSeries(req.series.daily) : null;
  let trades = COMBO.microRun(h1, b5, btc, req.start).filter(function (t) { return !t.open; });
  const last = b5.t.length ? b5.c[b5.c.length - 1] : null;
  const lastExit = b5.t.length ? b5.t[b5.t.length - 1] + 300000 : req.end;
  trades = trades.map(function (t) {
    if (t.exit_ms > req.end && last != null) {
      const gross = t.qty * (last - t.entry_px) * t.side;
      const fee = FEE * t.qty * (t.entry_px + last);
      return Object.assign({}, t, { exit_ms: lastExit, exit_px: last, pnl0: gross, fee: fee, why: "END" });
    }
    return t;
  }).filter(function (t) { return t.entry_ms < req.end && t.exit_ms >= req.start; });
  if (series) trades = trades.filter(function (t) { return colourOf(series, t.entry_ms) === t.side; });
  trades = oneAtATime(trades);
  const out = trades.map(function (t) {
    return row({
      side: t.side, qty: t.qty * scale, entry_ms: t.entry_ms, exit_ms: t.exit_ms,
      entry: t.entry_px, exit: t.exit_px, stop: t.sl, tp: t.tp,
      entry_reason: "seq5", exit_reason: t.why,
      gross: t.pnl0 * scale, fee: t.fee * scale, net: (t.pnl0 - t.fee) * scale
    });
  });
  const risk = UNIT.Micro * scale;
  return { trades: out, meta: { scale: scale, note: (req.venue === "pivex"
    ? "Pivex single entry. Micro risk $" + risk.toFixed(2) + " (125 × weight × k=" + req.k + "). One stop, one target, one trade at a time."
    : "Binance single entry. Micro risk $125, not scaled by k. The strategy has no add.") } };
}

function turtle(req) {
  const rows5 = bars(req.series.m5, 300000);
  const D = COMBO.barsOf(rows5);
  let S = COMBO.turtleSignals(D);
  if (req.daily_filter && req.venue === "pivex") {
    const series = colourSeries(req.series.daily);
    const allowL = S.allowL.slice(), allowS = S.allowS.slice();
    for (let i = 0; i < D.t.length; i++) {
      const col = colourOf(series, D.t[i] + 300000);
      allowL[i] = allowL[i] && col === 1;
      allowS[i] = allowS[i] && col === -1;
    }
    S = Object.assign({}, S, { allowL: allowL, allowS: allowS });
  }
  const scale = scaleOf("turtle_5m", req.venue, req.k);
  const single = req.venue === "pivex";
  const raw = COMBO.turtleRun(D, S, req.start, 3, 6, 3, single);
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
      if (exitMs < req.start || entryMs >= req.end) continue;
      const gross = t.pnl * scale;
      const net = Pf.tr[j].pnl * scale;
      trades.push(row({
        side: t.side, qty: t.q * scale, entry_ms: entryMs, exit_ms: exitMs,
        entry: t.ep, exit: t.xp, stop: null, tp: null,
        entry_reason: t.tag, exit_reason: t.why,
        gross: gross, fee: gross - net, net: net, tranche: t.tag.indexOf("ADD") >= 0 ? 2 : 1
      }));
    }
  }
  const full = UNIT.Turtle * scale;
  const note = single
    ? "Pivex single entry. Full book $" + full.toFixed(0) + " (10,000 × weight × k=" + req.k + ") in one fill. One stop, one 6% target. The +3% add is off."
    : "Binance multi-entry. Full book $10,000. First fill is half, the +3% add is the other half. TP1 closes only the first half.";
  return { trades: trades, meta: { scale: scale, note: note } };
}

function ml(req) {
  const rows5 = bars(req.series.m5, 300000);
  const D = COMBO.barsOf(rows5);
  const sig = COMBO.mlSignals(D);
  let sL = sig.sigL, sS = sig.sigS;
  if (req.daily_filter && req.venue === "pivex") {
    const series = colourSeries(req.series.daily);
    sL = sig.sigL.map(function (v, i) { return v && colourOf(series, D.t[i] + 300000) === 1; });
    sS = sig.sigS.map(function (v, i) { return v && colourOf(series, D.t[i] + 300000) === -1; });
  }
  const scale = scaleOf("ml_5m", req.venue, req.k);
  const closed = COMBO.flattenML(D, sL, sig.bssL, sS, sig.bssS, req.start);
  const trades = closed.filter(function (t) { return keep(t, req.start, req.end); }).map(function (t) {
    return row({
      side: t.side, qty: t.qty * scale, entry_ms: t.entry_ms, exit_ms: t.exit_ms,
      entry: t.entry_px, exit: t.exit_px, stop: null, tp: null,
      entry_reason: "Lorentz", exit_reason: t.why,
      gross: t.pnl0 * scale, fee: t.fee * scale, net: (t.pnl0 - t.fee) * scale
    });
  });
  const notional = UNIT.ML * scale;
  return { trades: trades, meta: { scale: scale, note: (req.venue === "pivex"
    ? "Pivex single entry. ML notional $" + notional.toFixed(0) + " (10,000 × weight × k=" + req.k + "). Stop 3%, target 3.1%, time stop 300 bars."
    : "Binance single entry. ML notional $10,000, not scaled by k. Stop 3%, target 3.1%, time stop 300 bars.") } };
}

function kyivParts(ms) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Kyiv", hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
  const o = {};
  fmt.formatToParts(new Date(ms)).forEach(function (p) { o[p.type] = p.value; });
  o.hour = String(+o.hour % 24);
  return o;
}

function kyivMidnight(ms) {
  const p = kyivParts(ms);
  let guess = Date.UTC(+p.year, +p.month - 1, +p.day) - 4 * 3600000;
  for (let i = 0; i < 8 * 60; i++) {
    const q = kyivParts(guess);
    if (+q.hour === 0 && +q.minute === 0 && +q.day === +p.day && +q.month === +p.month && +q.year === +p.year) return guess;
    guess += 60000;
  }
  throw new Error("could not resolve Kyiv midnight");
}

function sol1m(req) {
  const src = (req.series.m1 || []).map(function (r) { return [r[0], r[1], r[2], r[3], r[4]]; });
  const eng = SOL1M.engine(src);
  const trades = [];
  if (src.length) {
    let day = kyivMidnight(src[0][0]);
    const last = src[src.length - 1][0] + 60000;
    while (day < last) {
      const info = SOL1M.dayInfo(src, day);
      const sim = SOL1M.daySim(src, eng.fd, info);
      sim.trades.forEach(function (t) { trades.push(t); });
      day = kyivMidnight(day + 26 * 3600000);
    }
  }
  const out = trades.filter(function (t) { return t.exit_ms >= req.start && t.entry_ms < req.end; }).map(function (t) {
    return row({
      side: t.dir, qty: t.qty, entry_ms: t.entry_ms, exit_ms: t.exit_ms,
      entry: t.entry, exit: t.exit, stop: t.sl, tp: null,
      entry_reason: "morning flip", exit_reason: t.reason,
      gross: t.pnl, fee: 0, net: t.pnl, adjustable: false
    });
  });
  return { trades: out, meta: { note: "SOL 1m Morning HAMA. Fixed $40,000 notional, $0.06 spread, −$100 stop. Not scaled by k. 08:00 Kyiv day filter, ±1% to ±3%." } };
}

const HANDLERS = {
  hama_30m: hama, hama_1h: hama, s5_15m: s5, er_grid: er, dg_grid: dg,
  hama_grid_5m: hg, hama_grid_1m: hg, micro_1h: micro, turtle_5m: turtle,
  ml_5m: ml, sol1m_morning: sol1m
};

function main() {
  const req = JSON.parse(fs.readFileSync(0, "utf8"));
  const fn = HANDLERS[req.id];
  if (!fn) throw new Error("unknown strategy " + req.id);
  req.k = req.k > 0 ? +req.k : 8;
  req.daily_filter = !!req.daily_filter;
  const out = fn(req);
  process.stdout.write(JSON.stringify(out));
}

if (require.main === module) {
  try { main(); }
  catch (err) {
    process.stderr.write(String(err && err.stack || err) + "\n");
    process.exit(1);
  }
} else {
  module.exports = { HANDLERS: HANDLERS };
}
