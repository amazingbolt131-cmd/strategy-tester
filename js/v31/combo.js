(function (root, factory) {
  var E = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = E;
  root.COMBO6 = E;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var nan = NaN;
  function isN(x) { return x !== x; }
  function arr(n, v) { var a = new Array(n); for (var i = 0; i < n; i++) a[i] = v; return a; }

  // ----- Pine / ML seeded EMA-RMA (SMA seed of first n non-NaN, then recurse; NaN after seed keeps prev) -----
  function smaSeedRec(x, n, alpha) {
    var out = arr(x.length, nan), s = nan, buf = [], i, v;
    for (i = 0; i < x.length; i++) {
      v = x[i];
      if (isN(v)) { out[i] = s; continue; }
      if (isN(s)) {
        buf.push(v);
        if (buf.length === n) { s = 0; for (var k = 0; k < buf.length; k++) s += buf[k]; s /= n; }
      } else s = alpha * v + (1 - alpha) * s;
      out[i] = s;
    }
    return out;
  }
  function ema(x, n) { return smaSeedRec(x, n, 2 / (n + 1)); }
  function rma(x, n) { return smaSeedRec(x, n, 1 / n); }
  function sma(x, n) {
    var out = arr(x.length, nan), i, j, s;
    for (i = n - 1; i < x.length; i++) {
      s = 0; for (j = i - n + 1; j <= i; j++) { if (isN(x[j])) { s = nan; break; } s += x[j]; }
      out[i] = s / n;
    }
    return out;
  }
  function shift(x, k) {
    k = k == null ? 1 : k;
    var out = arr(x.length, nan);
    for (var i = k; i < x.length; i++) out[i] = x[i - k];
    return out;
  }
  function nz(x, v) { v = v == null ? 0 : v; var o = new Array(x.length); for (var i = 0; i < x.length; i++) o[i] = isN(x[i]) ? v : x[i]; return o; }
  function rollingMax(x, n) {
    var out = arr(x.length, nan), i, j, m;
    for (i = n - 1; i < x.length; i++) { m = -Infinity; for (j = i - n + 1; j <= i; j++) { if (isN(x[j])) { m = nan; break; } if (x[j] > m) m = x[j]; } out[i] = m; }
    return out;
  }
  function rollingMin(x, n) {
    var out = arr(x.length, nan), i, j, m;
    for (i = n - 1; i < x.length; i++) { m = Infinity; for (j = i - n + 1; j <= i; j++) { if (isN(x[j])) { m = nan; break; } if (x[j] < m) m = x[j]; } out[i] = m; }
    return out;
  }
  function trueRange(h, l, c) {
    var pc = shift(c), tr = new Array(c.length), i, a, b, d;
    for (i = 0; i < c.length; i++) {
      a = h[i] - l[i]; b = Math.abs(h[i] - pc[i]); d = Math.abs(l[i] - pc[i]);
      tr[i] = a > b ? (a > d ? a : d) : (b > d ? b : d);
    }
    tr[0] = h[0] - l[0];
    return tr;
  }
  function atr(h, l, c, n) { return rma(trueRange(h, l, c), n); }
  function rsi(src, n) {
    var ch = new Array(src.length), u = new Array(src.length), d = new Array(src.length), i, x;
    ch[0] = u[0] = d[0] = nan;
    for (i = 1; i < src.length; i++) {
      x = src[i] - src[i - 1];
      if (isN(src[i]) || isN(src[i - 1])) { ch[i] = u[i] = d[i] = nan; }
      else { ch[i] = x; u[i] = x > 0 ? x : 0; d[i] = x < 0 ? -x : 0; }
    }
    var ru = rma(u, n), rd = rma(d, n), out = arr(src.length, nan);
    for (i = 0; i < src.length; i++) {
      if (isN(ru[i]) || isN(rd[i])) continue;
      if (rd[i] === 0) out[i] = 100;
      else if (ru[i] === 0) out[i] = 0;
      else out[i] = 100 - 100 / (1 + ru[i] / rd[i]);
    }
    return out;
  }

  // ----- Supertrend (pine.py _supertrend): direction -1 = uptrend, +1 = downtrend -----
  function supertrend(h, l, c, factor, n) {
    var at = atr(h, l, c, n), N = c.length;
    var dirn = arr(N, 1), st = arr(N, nan), lo = arr(N, nan), up = arr(N, nan);
    for (var i = 0; i < N; i++) {
      var hl2 = (h[i] + l[i]) / 2, u = hl2 + factor * at[i], d = hl2 - factor * at[i];
      if (i > 0 && !isN(lo[i - 1])) {
        d = (d > lo[i - 1] || c[i - 1] < lo[i - 1]) ? d : lo[i - 1];
        u = (u < up[i - 1] || c[i - 1] > up[i - 1]) ? u : up[i - 1];
      }
      lo[i] = d; up[i] = u;
      if (i === 0 || isN(at[i - 1])) dirn[i] = 1;
      else if (st[i - 1] === up[i - 1]) dirn[i] = c[i] > u ? -1 : 1;
      else dirn[i] = c[i] < d ? 1 : -1;
      st[i] = dirn[i] === -1 ? d : u;
    }
    return { st: st, dir: dirn };
  }

  // ----- Daily raw HAMA colour (simlib.hama_co_cc, green if cc >= co) -----
  var HAMA_CO = 25, HAMA_CC = 20;
  function asCols(bars) {
    if (!bars) return bars;
    if (!Array.isArray(bars) && bars.t && bars.o) return bars;
    return barsOf(bars);
  }
  function hamaCoCc(bars) {
    var B = asCols(bars), N = B.t.length, mid = arr(N, nan), typ = new Array(N), i;
    for (i = 0; i < N; i++) {
      typ[i] = (B.o[i] + B.h[i] + B.l[i] + B.c[i]) / 4;
      if (i > 0) mid[i] = (B.o[i - 1] + B.c[i - 1]) / 2;
    }
    return { co: ema(mid, HAMA_CO), cc: ema(typ, HAMA_CC) };
  }
  function dailyColourSeries(dailyBars, tfMs) {
    tfMs = tfMs == null ? 86400000 : tfMs;
    var B = asCols(dailyBars), h = hamaCoCc(B), ct = new Array(B.t.length), g = new Array(B.t.length), i;
    for (i = 0; i < B.t.length; i++) {
      ct[i] = B.t[i] + tfMs;
      g[i] = (isN(h.co[i]) || isN(h.cc[i])) ? nan : (h.cc[i] >= h.co[i] ? 1 : -1);
    }
    return { ct: ct, g: g };
  }
  function colourAt(series, ms) {
    var ct = series.ct, lo = 0, hi = ct.length - 1, k = -1, m;
    while (lo <= hi) { m = (lo + hi) >> 1; if (ct[m] <= ms) { k = m; lo = m + 1; } else hi = m - 1; }
    if (k < 0 || isN(series.g[k])) return 0;
    return series.g[k] === 1 ? 1 : -1;
  }

  // ----- TV bar-path fill (tv_top2_1122/engine.py) -----
  function path(o, h, l, c) { return (h - o) <= (o - l) ? [o, h, l, c] : [o, l, h, c]; }
  function walk(pxPath, levelsFn, onFill) {
    var p0 = pxPath[0], hit, list, i, item;
    while (true) {
      hit = null; list = levelsFn();
      for (i = 0; i < list.length; i++) {
        item = list[i];
        if (item.kind === 'tp' && ((item.side === 1 && p0 >= item.L) || (item.side === -1 && p0 <= item.L))) { hit = item; break; }
        if (item.kind === 'sl' && ((item.side === 1 && p0 <= item.L) || (item.side === -1 && p0 >= item.L))) { hit = item; break; }
      }
      if (!hit) break;
      onFill(hit.key, p0, hit.kind, true);
    }
    for (var s = 0; s < pxPath.length - 1; s++) {
      var a = pxPath[s], b = pxPath[s + 1], cur = a;
      while (true) {
        var best = null; list = levelsFn();
        for (i = 0; i < list.length; i++) {
          item = list[i];
          var L = item.L, up = b > cur;
          if ((up && cur < L && L <= b) || (!up && b <= L && L < cur)) {
            var trig = (item.kind === 'tp' && item.side === 1 && up) || (item.kind === 'tp' && item.side === -1 && !up) ||
                       (item.kind === 'sl' && item.side === 1 && !up) || (item.kind === 'sl' && item.side === -1 && up);
            if (trig) {
              var dist = Math.abs(L - cur);
              if (!best || dist < best.d) best = { d: dist, key: item.key, kind: item.kind, L: L };
            }
          }
        }
        if (!best) break;
        onFill(best.key, best.L, best.kind, false); cur = best.L;
      }
    }
  }

  // ===================== Pivex Micro =====================
  function microSignal(o, h, l, c) {
    var fe = ema(c, 21), se = ema(c, 55), a = atr(h, l, c, 10), r = rsi(c, 14);
    var sd = supertrend(h, l, c, 2.5, 10).dir;
    var basis = ema(c, 20), N = c.length;
    var L = new Array(N), S = new Array(N), q = new Array(N), i, lb, ub, lrc, src, ls, ss;
    for (i = 0; i < N; i++) {
      ub = basis[i] + a[i]; lb = basis[i] - a[i];
      ls = (fe[i] > se[i] ? 1 : 0) + (sd[i] < 0 ? 1 : 0) + (r[i] > 50 ? 1 : 0) + (c[i] > basis[i] ? 1 : 0);
      ss = (fe[i] < se[i] ? 1 : 0) + (sd[i] > 0 ? 1 : 0) + (r[i] < 50 ? 1 : 0) + (c[i] < basis[i] ? 1 : 0);
      var pl = i ? l[i - 1] : nan, pc = i ? c[i - 1] : nan, plb = i ? (basis[i - 1] - a[i - 1]) : nan;
      var ph = i ? h[i - 1] : nan, pub = i ? (basis[i - 1] + a[i - 1]) : nan;
      lrc = (pl <= plb) && (pc <= plb) && (c[i] > lb) && (c[i] > o[i]);
      src = (ph >= pub) && (pc >= pub) && (c[i] < ub) && (c[i] < o[i]);
      L[i] = !!(lrc && ls >= 3); S[i] = !!(src && ss >= 3);
      q[i] = L[i] ? ls : ss;
    }
    return { long: L, short: S, atr: a, quality: q };
  }
  // Search 5m bars at/after et (open time). Stop first if both in one bar. Returns {xtOpen, xp} or null if still open.
  function microExit(b5, et, side, sl, tp) {
    var t = b5.t, lo = 0, hi = t.length - 1, j = t.length, m;
    while (lo <= hi) { m = (lo + hi) >> 1; if (t[m] < et) lo = m + 1; else { j = m; hi = m - 1; } }
    for (; j < t.length; j++) {
      var hiP = b5.h[j], loP = b5.l[j];
      var hs = side === 1 ? (loP <= sl) : (hiP >= sl);
      var ht = side === 1 ? (hiP >= tp) : (loP <= tp);
      if (hs) return { xtOpen: t[j], xp: sl, why: 'SL' };
      if (ht) return { xtOpen: t[j], xp: tp, why: 'TP' };
    }
    return null;
  }
  function searchLastLe(ct, ms) {
    var lo = 0, hi = ct.length - 1, k = -1, m;
    while (lo <= hi) { m = (lo + hi) >> 1; if (ct[m] <= ms) { k = m; lo = m + 1; } else hi = m - 1; }
    return k;
  }
  function microRun(h1, b5, btcDaily, startMs) {
    var o = h1.o, h = h1.h, l = h1.l, c = h1.c, t = h1.t, sg = microSignal(o, h, l, c);
    var brsi = rsi(btcDaily.c, 14), bct = btcDaily.t.map(function (x) { return x + 86400000; });
    var trades = [], i, side, et, k, r, ep, sl, tp, notl, q, ex;
    for (i = 0; i < t.length; i++) {
      if (!(sg.long[i] || sg.short[i])) continue;
      if (i + 1 >= t.length) continue;
      side = sg.long[i] ? 1 : -1;
      et = t[i + 1];
      if (et < startMs) continue;
      k = searchLastLe(bct, et); r = k >= 0 ? brsi[k] : nan;
      if ((side === 1 && r < 30) || (side === -1 && r > 70)) continue;
      ep = o[i + 1]; sl = ep - side * 1.5 * sg.atr[i]; tp = ep + side * 3.0 * sg.atr[i];
      notl = 125.0 / (1.5 * sg.atr[i] / ep + 0.0006); q = notl / ep;
      ex = microExit(b5, et, side, sl, tp);
      if (!ex) { trades.push({ open: true, side: side, entry_ms: et, entry_px: ep, sl: sl, tp: tp, qty: q, notional: notl, atr: sg.atr[i] }); continue; }
      trades.push({
        open: false, side: side, entry_ms: et, exit_ms: ex.xtOpen + 300000,
        entry_px: ep, exit_px: ex.xp, sl: sl, tp: tp, qty: q, notional: notl,
        pnl0: q * (ex.xp - ep) * side, fee: 0.0004 * q * (ep + ex.xp), why: ex.why
      });
    }
    return trades;
  }

  // ===================== ML Lorentz =====================
  function cci(src, n) {
    var m = sma(src, n), dev = arr(src.length, nan), out = arr(src.length, nan), i, j, s, w;
    for (i = n - 1; i < src.length; i++) {
      s = 0; for (j = i - n + 1; j <= i; j++) s += Math.abs(src[j] - m[i]);
      dev[i] = s / n;
      out[i] = (src[i] - m[i]) / (0.015 * dev[i]);
    }
    return out;
  }
  function normalize(src, lo, hi) {
    var hmin = 10e10, hmax = -10e10, out = arr(src.length, nan), i, v;
    for (i = 0; i < src.length; i++) {
      v = src[i];
      if (!isN(v)) { if (v < hmin) hmin = v; if (v > hmax) hmax = v; }
      out[i] = lo + (hi - lo) * (v - hmin) / Math.max(hmax - hmin, 10e-10);
    }
    return out;
  }
  function rescale(src, omin, omax, nmin, nmax) {
    var out = new Array(src.length), den = Math.max(omax - omin, 10e-10), i;
    for (i = 0; i < src.length; i++) out[i] = nmin + (nmax - nmin) * (src[i] - omin) / den;
    return out;
  }
  function n_rsi(c, a, b) { return rescale(ema(rsi(c, a), b), 0, 100, 0, 1); }
  function n_cci(c, a, b) { return normalize(ema(cci(c, a), b), 0, 1); }
  function n_wt(src, a, b) {
    var e1 = ema(src, a), diff = new Array(src.length), i;
    for (i = 0; i < src.length; i++) diff[i] = Math.abs(src[i] - e1[i]);
    var e2 = ema(diff, a), ci = new Array(src.length);
    for (i = 0; i < src.length; i++) ci[i] = (src[i] - e1[i]) / (0.015 * e2[i]);
    var w1 = ema(ci, b), w2 = sma(w1, 4), d = new Array(src.length);
    for (i = 0; i < src.length; i++) d[i] = w1[i] - w2[i];
    return normalize(d, 0, 1);
  }
  function n_adx(h, l, c, n) {
    var c1 = nz(shift(c)), h1 = nz(shift(h)), l1 = nz(shift(l));
    var N = h.length, tr = new Array(N), dmp = new Array(N), dmn = new Array(N), i;
    for (i = 0; i < N; i++) {
      var a = h[i] - l[i], b = Math.abs(h[i] - c1[i]), d = Math.abs(l[i] - c1[i]);
      tr[i] = a > b ? (a > d ? a : d) : (b > d ? b : d);
      dmp[i] = (h[i] - h1[i] > l1[i] - l[i]) ? Math.max(h[i] - h1[i], 0) : 0;
      dmn[i] = (l1[i] - l[i] > h[i] - h1[i]) ? Math.max(l1[i] - l[i], 0) : 0;
    }
    var ts = new Array(N), sp = new Array(N), sn = new Array(N), pt = 0, ps = 0, pn = 0, dx = new Array(N);
    for (i = 0; i < N; i++) {
      pt = pt - pt / n + tr[i]; ps = ps - ps / n + dmp[i]; pn = pn - pn / n + dmn[i];
      ts[i] = pt; sp[i] = ps; sn[i] = pn;
      var dip = sp[i] / ts[i] * 100, din = sn[i] / ts[i] * 100;
      dx[i] = Math.abs(dip - din) / (dip + din) * 100;
    }
    return rescale(rma(dx, n), 0, 100, 0, 1);
  }
  function filterVolatility(h, l, c, mn, mx) {
    var tr = trueRange(h, l, c), a = rma(tr, mn), b = rma(tr, mx), out = new Array(h.length);
    for (var i = 0; i < h.length; i++) out[i] = a[i] > b[i];
    return out;
  }
  function regimeFilter(src, h, l, thr) {
    var N = src.length, v1 = arr(N, nan), v2 = arr(N, nan), k = arr(N, nan), i;
    for (i = 0; i < N; i++) {
      var p1 = (i === 0 || isN(v1[i - 1])) ? 0 : v1[i - 1];
      var p2 = (i === 0 || isN(v2[i - 1])) ? 0 : v2[i - 1];
      var prev = i > 0 ? src[i - 1] : nan;
      v1[i] = 0.2 * (src[i] - prev) + 0.8 * p1;
      v2[i] = 0.1 * (h[i] - l[i]) + 0.8 * p2;
      var om = Math.abs(v1[i] / v2[i]);
      var al = (-om * om + Math.sqrt(om * om * om * om + 16 * om * om)) / 8;
      var pk = (i === 0 || isN(k[i - 1])) ? 0 : k[i - 1];
      k[i] = al * src[i] + (1 - al) * pk;
    }
    var sl = new Array(N); sl[0] = nan;
    for (i = 1; i < N; i++) sl[i] = Math.abs(k[i] - k[i - 1]);
    var e = ema(sl, 200), out = new Array(N);
    for (i = 0; i < N; i++) {
      var ns = (sl[i] - e[i]) / e[i];
      out[i] = ns >= thr; // NaN -> false
    }
    return out;
  }
  function rationalQuadratic(src, lb, rw, start) {
    var N = src.length, out = arr(N, nan), nI = 1 + start + 1, i, t, num, den, w, y;
    var iw = new Array(nI);
    for (i = 0; i < nI; i++) iw[i] = Math.pow(1 + (i * i) / ((lb * lb) * 2 * rw), -rw);
    for (t = 0; t < N; t++) {
      if (t - (nI - 1) < 0) continue;
      num = 0; den = 0;
      for (i = 0; i < nI; i++) { y = src[t - i]; w = iw[i]; num += y * w; den += w; }
      out[t] = num / den;
    }
    return out;
  }
  function knn(F, src, nb, mbb, gateFrom) {
    var N = F.length, K = F[0].length, pred = arr(N, 0), lab = arr(N, 0);
    var preds = new Float64Array(4096), dists = new Float64Array(4096), m = 0, t, i, f, d, p = 0, last, sizeLoop, start, L, j;
    for (t = 0; t < N; t++) {
      if (t >= 4) lab[t] = src[t - 4] < src[t] ? -1 : (src[t - 4] > src[t] ? 1 : 0);
    }
    for (t = 0; t < N; t++) {
      L = Math.min(t + 1, mbb); start = t + 1 - L;
      sizeLoop = Math.min(mbb - 1, L - 1);
      if (t >= gateFrom && sizeLoop >= 0) {
        last = -1.0;
        for (i = 0; i <= sizeLoop; i++) {
          if (i % 4 !== 0) continue;
          d = 0;
          for (f = 0; f < K; f++) d += Math.log(1 + Math.abs(F[t][f] - F[start + i][f]));
          if (d >= last) {
            last = d; preds[m] = lab[t - i] || 0; dists[m] = d; m += 1;
            if (m > nb && m > nb * 3 / 4) {
              last = dists[Math.round(nb * 3 / 4)];
              for (j = 0; j < m - 1; j++) { preds[j] = preds[j + 1]; dists[j] = dists[j + 1]; }
              m -= 1;
            }
          }
        }
        p = 0; for (j = 0; j < m; j++) p += preds[j];
      }
      pred[t] = p;
    }
    return pred;
  }
  function bssOf(sig) {
    var N = sig.length, b = arr(N, 1e9), last = -1, i;
    for (i = 0; i < N; i++) { if (sig[i]) last = i; if (last >= 0) b[i] = i - last; }
    return b;
  }
  function mlSignals(D) {
    var o = D.o, h = D.h, l = D.l, c = D.c, N = c.length, i;
    var hlc3 = new Array(N), ohlc4 = new Array(N);
    for (i = 0; i < N; i++) { hlc3[i] = (h[i] + l[i] + c[i]) / 3; ohlc4[i] = (o[i] + h[i] + l[i] + c[i]) / 4; }
    var F = new Array(N);
    var f0 = n_rsi(c, 14, 1), f1 = n_wt(hlc3, 10, 11), f2 = n_cci(c, 20, 1), f3 = n_adx(h, l, c, 20), f4 = n_rsi(c, 9, 1);
    for (i = 0; i < N; i++) F[i] = [f0[i], f1[i], f2[i], f3[i], f4[i]];
    var pred = knn(F, c, 12, 2000, 0);
    var vol = filterVolatility(h, l, c, 1, 10), reg = regimeFilter(ohlc4, h, l, -0.1);
    var yh = rationalQuadratic(c, 8, 8.0, 25), yh1 = shift(yh);
    var sigS = new Array(N), sigL = new Array(N);
    for (i = 0; i < N; i++) {
      sigS[i] = (pred[i] < 0) && (yh1[i] > yh[i]) && vol[i] && reg[i];
      sigL[i] = (pred[i] > 0) && (yh1[i] < yh[i]) && vol[i] && reg[i];
    }
    return { sigL: sigL, sigS: sigS, bssL: bssOf(sigL), bssS: bssOf(sigS), pred: pred };
  }
  var ML_TICK = 0.01;
  function mlBoth(D, sigL, bssL, sigS, bssS, startMs, SLp, TPp, MAXB) {
    SLp = SLp == null ? 3.0 : SLp; TPp = TPp == null ? 3.1 : TPp; MAXB = MAXB == null ? 300 : MAXB;
    var o = D.o, h = D.h, l = D.l, c = D.c, tc = D.ct, N = o.length;
    var pos = [], P = null, pend = null, pclose = false, k;
    for (k = 0; k < N; k++) {
      if (P && pclose) {
        var sd0 = P.side;
        P.tr[0].xb = k; P.tr[0].xp = o[k] - sd0 * ML_TICK; P.tr[0].why = 'MaxBars';
        pos.push(P); P = null; pclose = false;
      }
      if (pend) {
        var sd = pend.sd, pk = pend.pk;
        P = { side: sd, active: false, tr: [{ tag: 'ML', side: sd, sb: pk, eb: k, ep: o[k] + sd * ML_TICK, sc: c[pk], frac: 1.0 }] };
        pend = null;
      }
      if (P && P.active) {
        var T = P.tr[0], sd = P.side, ep = T.ep;
        var sl = ep * (1 - sd * SLp / 100), tp = ep * (1 + sd * TPp / 100), done = 0;
        walk(path(o[k], h[k], l[k], c[k]), function () {
          return done ? [] : [{ key: 's', kind: 'sl', L: sl, side: sd }, { key: 's', kind: 'tp', L: tp, side: sd }];
        }, function (key, px, kind) {
          done = 1; T.xb = k; T.xp = (kind === 'sl') ? (px - sd * ML_TICK) : px; T.why = kind === 'sl' ? 'SL' : 'TP';
        });
        if (done) { pos.push(P); P = null; }
      }
      if (!P) {
        if (tc[k] + 1 > startMs && k + 1 < N) {
          if (sigL[k]) pend = { sd: 1, pk: k };
          else if (sigS[k]) pend = { sd: -1, pk: k };
        }
      } else {
        P.active = true;
        var b = P.side === 1 ? bssL : bssS;
        if (b[k] >= MAXB && k + 1 < N) pclose = true;
      }
    }
    return pos;
  }
  function sizeNorm(pos, D, fee) {
    // basis norm_0 or norm_fee; qty = 10000 * frac / ep  (fixed $10k notional per full position)
    var out = [], i, j;
    for (i = 0; i < pos.length; i++) {
      var P = pos[i], trs = [];
      for (j = 0; j < P.tr.length; j++) {
        var t = P.tr[j], q = 10000 * t.frac / t.ep, x = {};
        for (var k in t) x[k] = t[k];
        x.q = q; x.fee_in = fee * q * t.ep;
        if (t.xb != null) x.pnl = q * (t.xp - t.ep) * t.side - fee * q * (t.ep + t.xp);
        trs.push(x);
      }
      out.push({ side: P.side, open: !!P.open, tr: trs });
    }
    return out;
  }
  function mlTradesFromPos(pos0, posF, D) {
    var trades = [], i;
    for (i = 0; i < pos0.length; i++) {
      var P0 = pos0[i], Pf = posF[i];
      if (P0.open || P0.tr.some(function (x) { return x.xb == null; })) continue;
      var tr = P0.tr, et = D.t[tr[0].eb], xb = tr[0].xb, j;
      for (j = 1; j < tr.length; j++) if (tr[j].xb > xb) xb = tr[j].xb;
      var xt = D.t[xb] + 300000, pnl0 = 0, fee = 0, q = 0, notional = 0;
      for (j = 0; j < tr.length; j++) { pnl0 += tr[j].pnl; q += tr[j].q; notional += tr[j].q * tr[j].ep; }
      for (j = 0; j < Pf.tr.length; j++) fee += (pos0[i].tr[j].pnl - Pf.tr[j].pnl);
      // fee = pnl0 - pnl_fee, and pos0 used fee=0 so tr[j].pnl is gross; posF has net. So fee = sum(p0.pnl)-sum(pf.pnl).
      trades.push({
        open: false, side: P0.side, entry_ms: et, exit_ms: xt,
        entry_px: tr[0].ep, exit_px: tr[tr.length - 1].xp, qty: q, notional: notional,
        pnl0: pnl0, fee: fee, why: tr[tr.length - 1].why
      });
    }
    // fix fee calculation properly
    for (i = 0; i < trades.length; i++) {
      var p0 = 0, pf = 0, P0 = pos0[i], Pf = posF[i];
      // skip opens already filtered — reindex: better recompute in the first loop. We'll just recompute below.
    }
    return trades;
  }
  function flattenML(D, sigL, bssL, sigS, bssS, startMs) {
    var raw = mlBoth(D, sigL, bssL, sigS, bssS, startMs);
    var sp0 = sizeNorm(raw, D, 0), spf = sizeNorm(raw, D, 0.0004);
    var trades = [], i, j;
    for (i = 0; i < sp0.length; i++) {
      var P0 = sp0[i], Pf = spf[i];
      if (P0.open || P0.tr.some(function (x) { return x.xb == null; })) continue;
      var tr = P0.tr, et = D.t[tr[0].eb], xb = tr[0].xb;
      for (j = 1; j < tr.length; j++) if (tr[j].xb > xb) xb = tr[j].xb;
      var pnl0 = 0, pnlF = 0, q = 0, notional = 0;
      for (j = 0; j < tr.length; j++) { pnl0 += tr[j].pnl; q += tr[j].q; notional += tr[j].q * tr[j].ep; }
      for (j = 0; j < Pf.tr.length; j++) pnlF += Pf.tr[j].pnl;
      trades.push({
        open: false, side: P0.side, entry_ms: et, exit_ms: D.t[xb] + 300000,
        entry_px: tr[0].ep, exit_px: tr[tr.length - 1].xp, qty: q, notional: notional,
        pnl0: pnl0, fee: pnl0 - pnlF, why: tr[tr.length - 1].why
      });
    }
    return trades;
  }

  // ===================== Reversal Turtle MTF =====================
  function nyParts(ms) {
    if (!nyParts._fmt) {
      nyParts._fmt = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York', hourCycle: 'h23',
        year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
      });
    }
    var o = {}; nyParts._fmt.formatToParts(new Date(ms)).forEach(function (p) { o[p.type] = p.value; });
    return o;
  }
  function sessDateKey(ms) {
    // (ny_timestamp - 17 hours).date  == if NY clock < 17:00, previous calendar date
    var o = nyParts(ms), hh = +o.hour % 24, mm = +o.minute;
    var off = (hh * 60 + mm) < 17 * 60 ? -1 : 0;
    return Date.UTC(+o.year, +o.month - 1, +o.day + off);
  }
  function turtleBias30m(D) {
    var t = D.t, o = D.o, h = D.h, l = D.l, c = D.c, N = t.length;
    // group into complete 30m buckets (exactly 6 five-minute bars)
    var buckets = [], i, bt, last = null, cur = null;
    for (i = 0; i < N; i++) {
      bt = t[i] - (t[i] % 1800000);
      if (!cur || cur.b !== bt) {
        if (cur) buckets.push(cur);
        cur = { b: bt, o: o[i], h: h[i], l: l[i], c: c[i], n: 1 };
      } else { if (h[i] > cur.h) cur.h = h[i]; if (l[i] < cur.l) cur.l = l[i]; cur.c = c[i]; cur.n++; }
    }
    if (cur) buckets.push(cur);
    var g = buckets.filter(function (x) { return x.n === 6; });
    var eH = [], eL = [], xH = [], xL = [];
    var sH = nan, sL = nan, sC = nan, pC = nan, atrv = nan, seed = 0, cnt = 0, skip = true, side = 0, fstop = nan;
    var S = new Array(g.length), R = new Array(g.length), prev = null, map = {};
    for (i = 0; i < g.length; i++) {
      var bo = g[i].o, bh = g[i].h, bl = g[i].l, bc = g[i].c, sess = sessDateKey(g[i].b);
      if (prev === null || sess !== prev) {
        if (!isN(sH)) {
          if (skip) skip = false;
          else {
            eH.push(sH); eL.push(sL); if (eH.length > 20) { eH.shift(); eL.shift(); }
            xH.push(sH); xL.push(sL); if (xH.length > 10) { xH.shift(); xL.shift(); }
            var trr = isN(pC) ? (sH - sL) : Math.max(sH - sL, Math.abs(sH - pC), Math.abs(sL - pC));
            if (isN(atrv)) { seed += trr; cnt += 1; if (cnt === 20) atrv = seed / 20; }
            else atrv = (atrv * 19 + trr) / 20;
            pC = sC;
          }
        }
        sH = bh; sL = bl; sC = bc;
      } else { sH = Math.max(sH, bh); sL = Math.min(sL, bl); sC = bc; }
      prev = sess;
      var ready = eH.length === 20 && xH.length === 10 && !isN(atrv);
      if (ready) {
        var EH = Math.max.apply(null, eH), EL = Math.min.apply(null, eL), LX = Math.min.apply(null, xL), SX = Math.max.apply(null, xH);
        if (side === 0) {
          var up = bh > EH, dn = bl < EL;
          if (up !== dn) {
            if (up) { var cand = Math.max(EH, bo) - 2 * atrv; if (bl >= cand) { side = 1; fstop = cand; } }
            else { var cand2 = Math.min(EL, bo) + 2 * atrv; if (bh <= cand2) { side = -1; fstop = cand2; } }
          }
        } else {
          var stv = side === 1 ? Math.max(fstop, LX) : Math.min(fstop, SX);
          if (side === 1 ? (bl < stv) : (bh > stv)) { side = 0; fstop = nan; }
        }
      }
      S[i] = side; R[i] = ready; map[g[i].b] = { s: side, r: ready };
    }
    var side5 = new Array(N), ready5 = new Array(N);
    for (i = 0; i < N; i++) {
      var pb = t[i] - (t[i] % 1800000) - 1800000, m = map[pb];
      side5[i] = m ? m.s : 0; ready5[i] = m ? m.r : false;
    }
    return { side: side5, ready: ready5 };
  }
  function turtleSignals(D) {
    var o = D.o, h = D.h, l = D.l, c = D.c, N = c.length, i;
    var bias = turtleBias30m(D);
    var basis = sma(c, 200), hi = rollingMax(h, 20), lo = rollingMin(l, 20), atrv = rma(trueRange(h, l, c), 14);
    var longT = new Array(N), shortT = new Array(N), sigatr = arr(N, nan);
    for (i = 0; i < N; i++) {
      var nearHi = c[i] >= hi[i] * (1 - 0.06), nearLo = c[i] <= lo[i] * (1 + 0.06);
      longT[i] = (c[i] < basis[i]) && nearLo; shortT[i] = (c[i] > basis[i]) && nearHi;
      if (longT[i] || shortT[i]) sigatr[i] = atrv[i];
    }
    var avg = sma(sigatr, 1000), atrd = new Array(N);
    for (i = 0; i < N; i++) atrd[i] = (isN(avg[i]) ? atrv[i] : avg[i]) * (70 / 3.0);
    var allowL = new Array(N), allowS = new Array(N);
    for (i = 0; i < N; i++) {
      allowL[i] = !!(bias.ready[i] && bias.side[i] === 1);
      allowS[i] = !!(bias.ready[i] && bias.side[i] === -1);
    }
    return { longT: longT, shortT: shortT, allowL: allowL, allowS: allowS, atrd: atrd, bias: bias.side };
  }
  function turtleRun(D, S, startMs, thrAdd, tp1, tp2, singleEntry) {
    thrAdd = thrAdd == null ? 3.0 : thrAdd; tp1 = tp1 == null ? 6.0 : tp1; tp2 = tp2 == null ? 3.0 : tp2;
    var frac = singleEntry ? 1 : 0.5;
    var o = D.o, h = D.h, l = D.l, c = D.c, tc = D.ct, N = o.length;
    var pos = [], P = null, pend = [], addUsed = false, k;
    function idOf(t) { return t._id; }
    var _uid = 1;
    for (k = 1; k < N; k++) {
      if (pend.length) {
        for (var p = 0; p < pend.length; p++) {
          var it = pend[p];
          if (!P) P = { side: it.side, tr: [] };
          P.tr.push({ tag: it.tag, side: it.side, sb: it.sb, eb: k, ep: o[k], sc: c[it.sb], frac: frac, _id: _uid++ });
        }
        pend = [];
      }
      if (P) {
        var ad = S.atrd[k - 1];
        function opn() { return P.tr.filter(function (t) { return t.xb == null; }); }
        walk(path(o[k], h[k], l[k], c[k]), function () {
          var ot = opn(); if (!ot.length) return [];
          var inv = 0, q; for (q = 0; q < ot.length; q++) inv += 1 / ot[q].ep;
          var avg = ot.length / inv, out = [], t;
          for (q = 0; q < ot.length; q++) {
            t = ot[q]; var tp = (t.tag === 'LONG' || t.tag === 'SHORT') ? tp1 : tp2;
            out.push({ key: t._id, kind: 'tp', L: avg * (1 + t.side * tp / 100), side: t.side });
            out.push({ key: t._id, kind: 'sl', L: avg - t.side * ad, side: t.side });
          }
          return out;
        }, function (key, px, kind) {
          var ot = opn(), q, t;
          for (q = 0; q < ot.length; q++) {
            t = ot[q];
            if (t._id === key) {
              t.xb = k; t.xp = px;
              t.why = kind === 'tp' ? ((t.tag === 'LONG' || t.tag === 'SHORT') ? 'TP1' : 'TP2') : 'Stop';
            }
          }
        });
        if (!opn().length) { pos.push(P); P = null; }
      }
      if (!P) {
        addUsed = false;
        if (tc[k] + 1 > startMs && k + 1 < N) {
          if (S.allowL[k] && S.longT[k]) pend = [{ tag: 'LONG', side: 1, sb: k }];
          else if (S.allowS[k] && S.shortT[k]) pend = [{ tag: 'SHORT', side: -1, sb: k }];
        }
      } else {
        var ot = P.tr.filter(function (t) { return t.xb == null; });
        if (ot.length) {
          var inv = 0, q; for (q = 0; q < ot.length; q++) inv += 1 / ot[q].ep;
          var avg = ot.length / inv, s = P.side, pm = (c[k] / avg - 1) * 100 * s;
          if (!singleEntry && !addUsed && pm >= thrAdd && k + 1 < N) {
            if ((s === 1 && S.allowL[k]) || (s === -1 && S.allowS[k])) {
              pend = [{ tag: s === 1 ? 'LONG_ADD' : 'SHORT_ADD', side: s, sb: k }]; addUsed = true;
            }
          }
        }
      }
    }
    if (P) { P.open = true; pos.push(P); }
    return pos;
  }
  function flattenTurtle(D, S, startMs) {
    var raw = turtleRun(D, S, startMs);
    var sp0 = sizeNorm(raw, D, 0), spf = sizeNorm(raw, D, 0.0004);
    var trades = [], i, j;
    for (i = 0; i < sp0.length; i++) {
      var P0 = sp0[i], Pf = spf[i];
      if (P0.open || P0.tr.some(function (x) { return x.xb == null; })) continue;
      var tr = P0.tr, et = D.t[tr[0].eb], xb = tr[0].xb;
      for (j = 1; j < tr.length; j++) if (tr[j].xb > xb) xb = tr[j].xb;
      var pnl0 = 0, pnlF = 0, q = 0, notional = 0;
      for (j = 0; j < tr.length; j++) { pnl0 += tr[j].pnl; q += tr[j].q; notional += tr[j].q * tr[j].ep; }
      for (j = 0; j < Pf.tr.length; j++) pnlF += Pf.tr[j].pnl;
      trades.push({
        open: false, side: P0.side, entry_ms: et, exit_ms: D.t[xb] + 300000,
        entry_px: tr[0].ep, exit_px: tr[tr.length - 1].xp, qty: q, notional: notional,
        pnl0: pnl0, fee: pnl0 - pnlF, why: tr[tr.length - 1].why
      });
    }
    return trades;
  }

  // ===================== Coordinator + sizing (combine.py) =====================
  var ORDER = ['Micro', 'Turtle', 'ML', 'HAMA30', 'HAMA1h', 'S5'];
  var BASE = { Micro: 1.0, Turtle: 1.0, ML: 1.0, HAMA30: 20.739227210345963, HAMA1h: 7.592660371373892, S5: 111.41632457034615 };
  var WEIGHTS = { Micro: 0.8340981490184549, Turtle: 0.15432856549524146, ML: 0.10408505559108173, HAMA30: 0.8652060249238318, HAMA1h: 1.57006260138179, S5: 1.1153394448639238 };
  var UNIT = { Micro: 125, Turtle: 10000, ML: 10000, HAMA30: 20.739227210345963, HAMA1h: 7.592660371373892, S5: 111.41632457034615 };
  function sizesAtK(k) {
    var o = {}, s;
    for (s in WEIGHTS) o[s] = UNIT[s] * WEIGHTS[s] * k;
    return o;
  }
  function accept(cands, rule) {
    rule = rule !== false;
    var c = cands.slice().map(function (r, i) { var x = {}; for (var k in r) x[k] = r[k]; x._i = i; x.pr = ORDER.indexOf(r.strategy); return x; });
    c.sort(function (a, b) { return a.entry_ms - b.entry_ms || a.pr - b.pr || a._i - b._i; });
    var busy = {}, microBusy = 0, own = {}, acc = [], skip = {}, i, r, key;
    ORDER.forEach(function (s) { skip[s] = 0; });
    for (i = 0; i < c.length; i++) {
      r = c[i]; key = r.strategy + '|' + r.symbol;
      if ((own[key] || 0) > r.entry_ms) continue;
      if (r.strategy === 'Micro' && microBusy > r.entry_ms) continue;
      if (rule && (busy[r.symbol] || 0) > r.entry_ms) { skip[r.strategy] += 1; r.skip_reason = 'coin busy (' + r.symbol + ')'; continue; }
      acc.push(r); busy[r.symbol] = Math.max(busy[r.symbol] || 0, r.exit_ms); own[key] = r.exit_ms;
      if (r.strategy === 'Micro') microBusy = r.exit_ms;
    }
    acc.sort(function (a, b) { return a.exit_ms - b.exit_ms; });
    var groups = {}, gi;
    for (i = 0; i < acc.length; i++) {
      key = acc[i].strategy + '|' + acc[i].symbol;
      if (!groups[key]) groups[key] = [];
      groups[key].push(acc[i]);
    }
    for (gi in groups) {
      var g = groups[gi], st = g[0].strategy, lvl, L, out, p;
      if (st === 'HAMA1h') {
        lvl = 1;
        for (i = 0; i < g.length; i++) {
          g[i].mult = lvl; p = g[i].pnl0;
          lvl = p < 0 ? Math.min(8, lvl + 1) : (p > 0 ? 1 : lvl);
        }
      } else if (st === 'S5') {
        L = 0;
        for (i = 0; i < g.length; i++) {
          g[i].mult = Math.min(2, Math.pow(2, Math.floor(L / 2)));
          L = (g[i].pnl0 - g[i].fee) < 0 ? L + 1 : 0;
        }
      } else {
        for (i = 0; i < g.length; i++) g[i].mult = 1;
      }
    }
    acc.sort(function (a, b) { return a.entry_ms - b.entry_ms || a.pr - b.pr; });
    return { accepted: acc, skip: skip };
  }
  function applyK(accepted, k) {
    k = k == null ? 8 : k;
    return accepted.map(function (r) {
      var x = {}; for (var key in r) x[key] = r[key];
      var w = WEIGHTS[r.strategy], m = (r.mult || 1) * BASE[r.strategy];
      x.usd0 = r.pnl0 * m;
      x.usdf = (r.pnl0 - r.fee) * m;
      x.usd0_k = x.usd0 * w * k;
      x.usdf_k = x.usdf * w * k;
      return x;
    });
  }

  function barsOf(rows) {
    // rows: [{t,o,h,l,c,ct?}] or {t:[],o:[],...}
    if (Array.isArray(rows)) {
      var t = [], o = [], h = [], l = [], c = [], ct = [], i, b, tf = null;
      if (rows.length >= 2) tf = rows[1].t - rows[0].t;
      for (i = 0; i < rows.length; i++) {
        b = rows[i]; t.push(b.t); o.push(b.o); h.push(b.h); l.push(b.l); c.push(b.c);
        ct.push(b.ct != null ? b.ct : (tf ? b.t + tf : b.t));
      }
      return { t: t, o: o, h: h, l: l, c: c, ct: ct };
    }
    return rows;
  }

  return {
    ema: ema, rma: rma, sma: sma, rsi: rsi, atr: atr, shift: shift,
    supertrend: supertrend, walk: walk, path: path,
    hamaCoCc: hamaCoCc, dailyColourSeries: dailyColourSeries, colourAt: colourAt,
    microSignal: microSignal, microRun: microRun, microExit: microExit,
    mlSignals: mlSignals, mlBoth: mlBoth, flattenML: flattenML, knn: knn,
    turtleSignals: turtleSignals, turtleRun: turtleRun, flattenTurtle: flattenTurtle, turtleBias30m: turtleBias30m,
    ORDER: ORDER, BASE: BASE, WEIGHTS: WEIGHTS, UNIT: UNIT,
    sizesAtK: sizesAtK, accept: accept, applyK: applyK, barsOf: barsOf, sizeNorm: sizeNorm,
    ML_TICK: ML_TICK
  };
});
