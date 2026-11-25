/* Strategy 7: SOL 1m Morning HAMA ($40k)  (id sol1m_morning)
 * Engine = exact port of /workspace/grokbot_replay_now/simlib.py engine() (HAMA 1m, CO 25 / CC 20, ATR 10 RMA,
 * gap 0.3 x ATR, 3m lock on the previous COMPLETED 3m HAMA colour) and of peaks/sim.py sim_day() with
 * tp=False, rules {maxloss:1, stop:100}, loss_limit 600, notional $40,000, spread $0.06 paid at entry.
 * Day rule (peaks/morning.py plan(8,(1,3),'skip','follow')): move = C[iT-1]/C[i0-1]-1 at 08:00 Kyiv;
 * +1..+3% LONG-only day, -1..-3% SHORT-only day, otherwise no trade. Data: Binance public SOLUSDT spot 1m. */
(function (root, factory) {
  var E = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = E;
  root.SOL1M = E;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var GAP = 0.3, LOCK_MIN = 3, CO_LEN = 25, CC_LEN = 20, ATR_LEN = 10;
  var CFG = { notional: 40000.0, spread: 0.06, stop: 100.0, maxloss: 1, lossLimit: 600.0, hourT: 8, bandLo: 1, bandHi: 3 };
  function ema(src, n) { // Pine ta.ema: SMA seed over first n non-null values, then alpha recursion (simlib.ema)
    var a = 2 / (n + 1), out = new Array(src.length), s = null, buf = [], i, x, k, t;
    for (i = 0; i < src.length; i++) {
      x = src[i];
      if (x === null) { out[i] = s; continue; }
      if (s === null) {
        buf.push(x);
        if (buf.length === n) { t = 0; for (k = 0; k < n; k++) t += buf[k]; s = t / n; }
      } else s = a * x + (1 - a) * s;
      out[i] = s;
    }
    return out;
  }
  function rma(src, n) { // simlib.rma
    var out = new Array(src.length), s = null, buf = [], i, k, t;
    for (i = 0; i < src.length; i++) {
      if (s === null) {
        buf.push(src[i]);
        if (buf.length === n) { t = 0; for (k = 0; k < n; k++) t += buf[k]; s = t / n; }
      } else s = (src[i] + (n - 1) * s) / n;
      out[i] = s;
    }
    return out;
  }
  // bars: array of [open_ms, o, h, l, c]
  function hamaCoCc(bars) {
    var N = bars.length, mid = new Array(N), typ = new Array(N), i, b;
    mid[0] = null;
    for (i = 1; i < N; i++) mid[i] = (bars[i - 1][1] + bars[i - 1][4]) / 2;
    for (i = 0; i < N; i++) { b = bars[i]; typ[i] = (b[1] + b[2] + b[3] + b[4]) / 4; }
    return { co: ema(mid, CO_LEN), cc: ema(typ, CC_LEN) };
  }
  function resample(bars, m) { // complete m-minute buckets only (simlib.resample)
    var ms = m * 60000, g = {}, keys = [], i, b, k, x, out = [];
    for (i = 0; i < bars.length; i++) {
      b = bars[i]; k = b[0] - b[0] % ms;
      if (!(k in g)) { g[k] = [k, b[1], b[2], b[3], b[4], 1]; keys.push(k); }
      else { x = g[k]; x[2] = Math.max(x[2], b[2]); x[3] = Math.min(x[3], b[3]); x[4] = b[4]; x[5] += 1; }
    }
    keys.sort(function (a, b2) { return a - b2; });
    for (i = 0; i < keys.length; i++) { x = g[keys[i]]; if (x[5] === m) out.push([x[0], x[1], x[2], x[3], x[4]]); }
    return out;
  }
  function engine(bars) { // -> { flips: [[open_ms, +1|-1, close]], fd: Int8 per bar, state }
    var N = bars.length, h = hamaCoCc(bars), co = h.co, cc = h.cc, tr = new Array(N), i, b, p;
    if (!N) return { flips: [], fd: [], state: 0 };
    tr[0] = bars[0][2] - bars[0][3];
    for (i = 1; i < N; i++) { p = bars[i - 1]; b = bars[i]; tr[i] = Math.max(b[2] - b[3], Math.abs(b[2] - p[4]), Math.abs(b[3] - p[4])); }
    var atr = rma(tr, ATR_LEN);
    var hb = resample(bars, LOCK_MIN), hh = hamaCoCc(hb), hgreen = {};
    for (i = 0; i < hb.length; i++) hgreen[hb[i][0]] = (hh.co[i] === null || hh.cc[i] === null) ? null : hh.cc[i] >= hh.co[i];
    var L = LOCK_MIN * 60000, state = 0, flips = [], fd = new Array(N), want, gap, hg, prev;
    for (i = 0; i < N; i++) {
      fd[i] = 0; b = bars[i];
      if (co[i] === null || cc[i] === null) continue;
      hg = hgreen[b[0] - b[0] % L - L];
      want = cc[i] >= co[i];
      gap = atr[i] !== null && Math.abs(cc[i] - co[i]) >= GAP * atr[i];
      var condG = want && gap && hg === true, condR = (!want) && gap && hg === false;
      prev = state;
      if (state === 0) state = want ? 1 : -1;
      else if (state === -1 && condG) state = 1;
      else if (state === 1 && condR) state = -1;
      if (prev === -1 && state === 1) { flips.push([b[0], 1, b[4]]); fd[i] = 1; }
      if (prev === 1 && state === -1) { flips.push([b[0], -1, b[4]]); fd[i] = -1; }
    }
    return { flips: flips, fd: fd, state: state };
  }
  function idx(bars, t) { // numpy searchsorted(O, t) (left)
    var lo = 0, hi = bars.length;
    while (lo < hi) { var m = (lo + hi) >> 1; if (bars[m][0] < t) lo = m + 1; else hi = m; }
    return lo;
  }
  function round2(x) { return Number(x.toFixed(2)); }
  // Day classification at 08:00 Kyiv. dayStart = Kyiv midnight ms.
  function dayInfo(bars, dayStart) {
    var i0 = idx(bars, dayStart), i1 = idx(bars, dayStart + 86400000), iT = idx(bars, dayStart + CFG.hourT * 3600000);
    var r = { i0: i0, i1: i1, iT: iT, dayStart: dayStart, ready: false, move: null, dir: 0, kind: 'wait' };
    if (i0 < 1 || iT < 1 || iT >= bars.length + 1) return r;
    if (!(bars.length && bars[bars.length - 1][0] + 60000 >= dayStart + CFG.hourT * 3600000)) return r; // 07:59 bar not closed yet
    var move = (bars[iT - 1][4] / bars[i0 - 1][4] - 1) * 100, a = Math.abs(move);
    r.ready = true; r.move = move;
    if (a >= CFG.bandLo && a <= CFG.bandHi) { r.dir = move > 0 ? 1 : -1; r.kind = move > 0 ? 'LONG' : 'SHORT'; }
    else r.kind = 'skip';
    return r;
  }
  // sim.py sim_day(tp=False, rules={maxloss:1, stop:100}) over bars iT..min(i1,N)-1; dayOver -> day_end close.
  function daySim(bars, fd, info) {
    var trades = [], real = 0, pos = null, nloss = 0, busted = false, done = false, why = null, i, N = bars.length;
    var res = { trades: trades, pnl: 0, pos: null, stopped: false, busted: false, why: null };
    if (!info.ready || !info.dir) return res;
    var d0 = info.dir, end = Math.min(info.i1, N), dayOver = info.i1 < N || (N && bars[N - 1][0] + 60000 >= info.dayStart + 86400000);
    function close(px, i, reason) {
      var pnl = (px - pos.e) * pos.q * pos.d; real += pnl;
      trades.push({ dir: pos.d, ei: pos.i, entry_ms: bars[pos.i][0], entry: pos.e, entry_close: pos.c, qty: pos.q, sl: pos.sl,
        xi: i, exit_ms: bars[i][0], exit: px, pnl: pnl, reason: reason });
      if (pnl < 0) nloss++;
      pos = null;
    }
    for (i = info.iT; i < end; i++) {
      var b = bars[i], h = b[2], l = b[3], c = b[4];
      if (pos) {
        var dd = pos.d, e = pos.e, q = pos.q, worst = dd === 1 ? l : h, wpnl = (worst - e) * q * dd, weq = real + wpnl;
        if (weq <= -CFG.lossLimit) { close(e + (-CFG.lossLimit - real) / q * dd, i, 'BUST'); busted = true; why = 'bust'; break; }
        var fl = real - CFG.stop;
        if (weq <= fl) close(e + (fl - real) / q * dd, i, 'stop');
      }
      if (CFG.maxloss && nloss >= CFG.maxloss) { done = true; why = 'maxloss'; }
      if (done) { if (pos) close(c, i, why); break; }
      var f = fd[i];
      if (f) {
        var cl = c;
        if (pos && pos.d !== f) close(cl, i, 'flip');
        if (CFG.maxloss && nloss >= CFG.maxloss) { done = true; why = 'maxloss'; break; }
        if (!pos && f === d0) {
          var ep = cl + CFG.spread * f, qq = round2(CFG.notional / cl);
          pos = { d: f, e: ep, q: qq, i: i, c: cl, sl: ep + (-CFG.stop) / qq * f };
        }
      }
    }
    if (pos && !busted && dayOver) close(bars[end - 1][4], end - 1, 'day_end');
    res.pnl = real; res.pos = pos; res.busted = busted; res.why = why; res.stopped = done || busted;
    return res;
  }
  var STATS = { starts: 91, from: '24 Aug', to: '22 Nov 2026', pass: 83, fail: 1, open: 7, pnl90: 1691, trades: 40, wins: 10, passDays: '21 Sep (29) and 15 Nov (54)', failDay: '15 Nov (start 8 Oct)' };
  return { CFG: CFG, STATS: STATS, ema: ema, rma: rma, hamaCoCc: hamaCoCc, resample: resample, engine: engine, idx: idx, dayInfo: dayInfo, daySim: daySim };
});
