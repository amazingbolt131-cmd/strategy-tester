/* Strengths tab: one run over every strategy on Pivex and Binance, ranked together. */
(function () {
  const tab = document.getElementById("tab-strengths");
  const panel = document.getElementById("strengths-panel");
  if (!tab || !panel) return;
  const tabSingle = document.getElementById("tab-single");
  const tabRanking = document.getElementById("tab-ranking");
  const singlePane = document.getElementById("single-pane");
  const rankingPanel = document.getElementById("ranking-panel");
  const btn = document.getElementById("start-strengths");
  const coinEl = document.getElementById("strengths-coin");
  const venueFilter = document.getElementById("strengths-venue");
  const wrap = document.getElementById("strengths-progress-wrap");
  const statusText = document.getElementById("strengths-status-text");
  const countEl = document.getElementById("strengths-count");
  const bar = document.getElementById("strengths-progress-bar");
  const errEl = document.getElementById("strengths-error");
  const reportEl = document.getElementById("strengths-report");
  const metaEl = document.getElementById("strengths-meta");
  const actions = document.getElementById("strengths-actions");
  const csvLink = document.getElementById("strengths-csv");
  const jsonLink = document.getElementById("strengths-json");
  let timer = null;
  let lastReport = null;

  function show(on) {
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
    panel.hidden = !on;
    if (on) {
      [tabSingle, tabRanking].forEach(function (t) {
        if (t) { t.classList.remove("active"); t.setAttribute("aria-selected", "false"); }
      });
      if (singlePane) singlePane.hidden = true;
      if (rankingPanel) rankingPanel.hidden = true;
    }
  }
  tab.addEventListener("click", function () { show(true); });
  [tabSingle, tabRanking].forEach(function (t) { if (t) t.addEventListener("click", function () { show(false); }); });

  function esc(s) {
    return String(s == null ? "" : s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  }
  function money(v, signed) {
    if (v == null) return "—";
    const n = Number(v);
    const t = "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return n < 0 ? "-" + t : (signed && n > 0 ? "+" + t : t);
  }
  function num(v, d) { return v == null ? "—" : Number(v).toFixed(d == null ? 2 : d); }
  function pct(v) { return v == null ? "—" : Number(v).toFixed(1) + "%"; }
  function tone(v) { return v == null || v === 0 ? "" : (v > 0 ? "pos" : "neg"); }
  function cell(value, text, cls) {
    return '<td data-value="' + esc(value == null ? "" : value) + '"' + (cls ? ' class="' + cls + '"' : "") + ">" + text + "</td>";
  }
  function medal(rank) { return '<span class="medal m' + rank + '">#' + rank + "</span>"; }

  function render(rep, jobId) {
    lastReport = rep;
    const s = rep.settings;
    metaEl.textContent = rep.symbol_label + " · " + rep.period_label + " · " + rep.window_label +
      " · Balance $" + Number(s.balance).toLocaleString("en-US") + " · Commission " + s.commission + "%/side · Slippage " + s.slippage +
      "% · Daily HAMA filter " + (s.daily_filter ? "ON (Pivex)" : "OFF") + " · " + rep.rows.length + " strategy × venue runs in " + rep.elapsed_s + "s";
    csvLink.href = "/strengths/" + encodeURIComponent(jobId) + ".csv";
    jsonLink.href = "/strengths/" + encodeURIComponent(jobId) + ".json";
    actions.hidden = false;
    draw();
  }

  function draw() {
    const rep = lastReport;
    if (!rep) return;
    const vf = venueFilter.value;
    const rows = rep.rows.filter(function (r) { return vf === "all" || r.venue === vf; });
    const byKey = {};
    rep.rows.forEach(function (r) { byKey[r.key] = r; });
    const nameOf = function (r) { return r.venue_label + " · " + r.strategy + " · " + r.timeframe; };
    let html = "";
    html += '<p class="muted">' + esc(rep.venue_rules.pivex) + "<br>" + esc(rep.venue_rules.binance) +
      "<br>Pivex and Binance size trades differently, so compare dollar figures across venues with care; win rate, PF, R:R and consistency compare directly. Categories need at least " +
      rep.min_trades + " trades; drawdown, fee, smoothness, speed and time-in-market categories also need a profit. Weeks are used for consistency on 1-month runs, months otherwise.</p>";

    // Category leaders
    html += '<h3>Category leaders</h3><div class="leaders">';
    rep.leaders.forEach(function (c) {
      const top = c.top.filter(function (t) { return vf === "all" || (byKey[t.key] && byKey[t.key].venue === vf); });
      html += '<div class="leader"><span class="leader-label">' + esc(c.label) + "</span>";
      if (!top.length) html += '<span class="muted">—</span>';
      top.forEach(function (t) { html += '<div class="leader-row">' + medal(t.rank) + " " + esc(nameOf(byKey[t.key])) + "</div>"; });
      html += "</div>";
    });
    html += "</div>";

    // Table
    const cols = ["Venue", "Strategy", "Timeframe", "Score", "Net", "Return", "PF", "Win %", "Max DD %", "Recovery", "R:R", "Expectancy", "Trades",
      "Max L streak", "Max W streak", "Green %", "P&L std", "Sharpe", "Sortino", "Avg hold h", "Long net", "Long win %", "Short net", "Short win %",
      "Best trade", "Worst trade", "Time in mkt", "Fees", "Fee drag", "Worst day", "Pivex $600 / $1,000", "Badges"];
    const types = cols.map(function (c, i) { return i < 3 || c === "Pivex $600 / $1,000" || c === "Badges" ? "text" : "number"; });
    html += '<h3>All strategies, ranked together</h3><p class="muted">Click a column to sort. Score is 3/2/1 points for each #1/#2/#3 category place.</p>';
    html += '<div class="table-wrap"><table id="strengths-table"><thead><tr>';
    cols.forEach(function (c, i) { html += '<th data-type="' + types[i] + '">' + esc(c) + "</th>"; });
    html += "</tr></thead><tbody>";
    const sorted = rows.slice().sort(function (a, b) {
      return (b.score || 0) - (a.score || 0) || ((b.metrics ? b.metrics.net_profit : -1e18) - (a.metrics ? a.metrics.net_profit : -1e18));
    });
    sorted.forEach(function (r) {
      const m = r.metrics;
      html += "<tr>" + cell(r.venue_label, esc(r.venue_label)) + cell(r.strategy, esc(r.strategy)) + cell(r.timeframe, esc(r.timeframe));
      if (!m) {
        html += cell(-1, "—") + '<td colspan="' + (cols.length - 4) + '" class="neg">Failed: ' + esc(r.error) + "</td></tr>";
        return;
      }
      const pfv = m.pf_infinite ? 1e9 : m.profit_factor;
      html += cell(r.score, String(r.score)) +
        cell(m.net_profit, money(m.net_profit, true), tone(m.net_profit)) +
        cell(m.return_pct, pct(m.return_pct), tone(m.return_pct)) +
        cell(pfv, m.pf_infinite ? "∞" : num(m.profit_factor), pfv == null ? "" : (pfv >= 1 ? "pos" : "neg")) +
        cell(m.win_rate, pct(m.win_rate)) +
        cell(m.max_dd_pct, pct(m.max_dd_pct), m.max_dd_pct ? "neg" : "") +
        cell(m.recovery_factor, num(m.recovery_factor)) +
        cell(m.rr, num(m.rr)) +
        cell(m.expectancy, money(m.expectancy, true), tone(m.expectancy)) +
        cell(m.trades, String(m.trades)) +
        cell(m.max_loss_streak, String(m.max_loss_streak)) +
        cell(m.max_win_streak, String(m.max_win_streak)) +
        cell(m.green_pct, pct(m.green_pct) + ' <span class="muted">' + esc(m.bucket) + "</span>") +
        cell(m.period_std, money(m.period_std)) +
        cell(m.sharpe, num(m.sharpe)) +
        cell(m.sortino, num(m.sortino)) +
        cell(m.avg_hold_h, num(m.avg_hold_h, 1)) +
        cell(m.long_net, m.long_trades ? money(m.long_net, true) : "—", tone(m.long_net)) +
        cell(m.long_win_rate, m.long_trades ? pct(m.long_win_rate) : "—") +
        cell(m.short_net, m.short_trades ? money(m.short_net, true) : "—", tone(m.short_net)) +
        cell(m.short_win_rate, m.short_trades ? pct(m.short_win_rate) : "—") +
        cell(m.best_trade, money(m.best_trade), "pos") +
        cell(m.worst_trade, money(m.worst_trade), m.worst_trade ? "neg" : "") +
        cell(m.time_in_market_pct, pct(m.time_in_market_pct)) +
        cell(m.fees, money(m.fees)) +
        cell(m.fee_drag_pct, pct(m.fee_drag_pct)) +
        cell(m.worst_day, money(m.worst_day), m.worst_day ? "neg" : "") +
        cell(m.pivex_info || "", esc(m.pivex_info || "—"), m.pivex_dd_breach ? "neg" : "") +
        cell((r.badges || []).length, (r.badges || []).map(function (b) { return '<span class="badge-inline">' + medal(b.rank) + " " + esc(b.label) + "</span>"; }).join(" ")) +
        "</tr>";
    });
    html += "</tbody></table></div>";

    // Good-for cards
    html += '<h3>Good for / weaknesses</h3><div class="strength-cards">';
    sorted.forEach(function (r) {
      html += '<div class="strength-card"><h4>' + esc(nameOf(r)) + "</h4>";
      if (!r.metrics) {
        html += '<p class="neg">Failed: ' + esc(r.error) + "</p></div>";
        return;
      }
      const goods = (r.badges || []).map(function (b) { return medal(b.rank) + " " + esc(b.text); }).concat((r.good_extra || []).map(esc));
      html += '<p class="muted">' + money(r.metrics.net_profit, true) + " · " + r.metrics.trades + " trades · win " + pct(r.metrics.win_rate) + " · max DD " + pct(r.metrics.max_dd_pct) + "</p>";
      html += "<strong>Good for</strong><ul>" + (goods.length ? goods.map(function (g) { return "<li>" + g + "</li>"; }).join("") : '<li class="muted">Nothing stands out in this period</li>') + "</ul>";
      html += "<strong>Weaknesses</strong><ul>" + ((r.weak || []).length ? r.weak.map(function (w) { return '<li class="neg">' + esc(w) + "</li>"; }).join("") : '<li class="muted">None flagged</li>') + "</ul>";
      if (r.note) html += '<p class="muted small">' + esc(r.note) + "</p>";
      html += "</div>";
    });
    html += "</div>";

    if (rep.skipped.length) {
      const sk = rep.skipped.filter(function (r) { return vf === "all" || r.venue === vf; });
      if (sk.length) {
        html += "<h3>Skipped (coin not offered)</h3><ul>";
        sk.forEach(function (r) { html += "<li>" + esc(r.venue_label + " · " + r.strategy) + ": " + esc(r.note) + "</li>"; });
        html += "</ul>";
      }
    }
    reportEl.innerHTML = html;
    reportEl.hidden = false;
    if (window.stMakeSortable) window.stMakeSortable(document.getElementById("strengths-table"));
  }
  venueFilter.addEventListener("change", draw);

  function busy(on) {
    btn.disabled = on;
    btn.textContent = on ? "Running…" : "Run strengths";
  }
  function poll(jobId) {
    fetch("/strengths/" + encodeURIComponent(jobId))
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Strengths run failed");
        const d = res.data;
        const total = d.total || 0;
        countEl.textContent = d.done + " / " + total + (d.skipped ? " (" + d.skipped + " skipped)" : "");
        bar.style.width = (total ? Math.round(d.done / total * 100) : 0) + "%";
        statusText.textContent = (d.status === "queued" ? "Queued · " : "") + (d.current || "") + " · " + d.elapsed_s + "s";
        if (d.status === "done") {
          clearInterval(timer); timer = null; busy(false);
          wrap.hidden = true;
          render(d.report, jobId);
        } else if (d.status === "error" || d.status === "cancelled") {
          throw new Error(d.error || "Strengths run " + d.status);
        }
      })
      .catch(function (err) {
        if (timer) { clearInterval(timer); timer = null; }
        busy(false);
        errEl.textContent = String(err.message || err);
        errEl.hidden = false;
      });
  }
  btn.addEventListener("click", function () {
    const val = function (id, dflt) { const el = document.getElementById(id); return el ? el.value : dflt; };
    const daily = document.getElementById("daily_hama");
    errEl.hidden = true;
    reportEl.hidden = true;
    actions.hidden = true;
    wrap.hidden = false;
    bar.style.width = "0%";
    statusText.textContent = "Starting…";
    countEl.textContent = "";
    busy(true);
    fetch("/strengths/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: coinEl.value,
        period: val("period", "1m"),
        balance: val("balance", "10000"),
        commission: val("commission", "0.04"),
        slippage: val("slippage", "0"),
        daily_hama: daily ? daily.checked : true,
      }),
    })
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.error || "Could not start");
        countEl.textContent = "0 / " + res.data.total;
        timer = setInterval(function () { poll(res.data.job_id); }, 1500);
      })
      .catch(function (err) {
        busy(false);
        wrap.hidden = true;
        errEl.textContent = String(err.message || err);
        errEl.hidden = false;
      });
  });
})();
