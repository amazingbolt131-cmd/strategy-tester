(function () {
  const catalogNode = document.getElementById("catalog");
  const strategySelect = document.querySelector("[name=strategy]");
  const venueSelect = document.getElementById("venue");
  const symbolSelect = document.getElementById("symbol");
  if (catalogNode && strategySelect && venueSelect && symbolSelect) {
    const catalog = JSON.parse(catalogNode.textContent);
    const allStrategies = Array.from(strategySelect.options).map(function (o) { return { value: o.value, label: o.textContent }; });
    function filterStrategies() {
      const v = venueSelect.value;
      const current = strategySelect.value;
      strategySelect.innerHTML = "";
      allStrategies.forEach(function (item) {
        const spec = catalog[item.value];
        if (!spec || !spec.venues[v] || !spec.venues[v].enabled) return;
        const option = document.createElement("option");
        option.value = item.value;
        option.textContent = item.label;
        if (item.value === current) option.selected = true;
        strategySelect.appendChild(option);
      });
    }
    function syncChoices() {
      const spec = catalog[strategySelect.value];
      if (!spec) return;
      const venue = spec.venues[venueSelect.value];
      const current = symbolSelect.value;
      symbolSelect.innerHTML = "";
      (venue ? venue.symbols : []).forEach(function (item) {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        if (item.id === current) option.selected = true;
        symbolSelect.appendChild(option);
      });
    }
    strategySelect.addEventListener("change", syncChoices);
    venueSelect.addEventListener("change", function () { filterStrategies(); syncChoices(); });
    filterStrategies();
    syncChoices();
  }

  const run = document.getElementById("run");
  const startBtn = document.getElementById("start-sweep");
  const rankBtn = document.getElementById("start-ranking");
  if (run && run.form) {
    run.form.addEventListener("submit", function () {
      run.disabled = true;
      run.textContent = "Running…";
      if (startBtn) startBtn.disabled = true;
      if (typeof rankBtn !== "undefined" && rankBtn) rankBtn.disabled = true;
    });
  }

  function makeSortable(table) {
    if (!table) return;
    const headers = Array.from(table.querySelectorAll("th"));
    headers.forEach(function (th, index) {
      th.addEventListener("click", function () {
        const type = th.dataset.type || "text";
        const body = table.tBodies[0];
        if (!body) return;
        const rows = Array.from(body.rows);
        const dir = th.dataset.dir === "asc" ? "desc" : "asc";
        headers.forEach(function (other) { other.dataset.dir = ""; });
        th.dataset.dir = dir;
        rows.sort(function (a, b) {
          const av = a.cells[index].dataset.value != null ? a.cells[index].dataset.value : "";
          const bv = b.cells[index].dataset.value != null ? b.cells[index].dataset.value : "";
          let cmp;
          if (type === "number") {
            const na = parseFloat(av);
            const nb = parseFloat(bv);
            if (isNaN(na) && isNaN(nb)) cmp = 0;
            else if (isNaN(na)) cmp = -1;
            else if (isNaN(nb)) cmp = 1;
            else cmp = na - nb;
          } else {
            cmp = av < bv ? -1 : av > bv ? 1 : 0;
          }
          return dir === "asc" ? cmp : -cmp;
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  }

  const table = document.getElementById("trades");
  makeSortable(table);

  const exportLink = document.getElementById("export");
  if (exportLink && table) {
    exportLink.addEventListener("click", function (event) {
      event.preventDefault();
      const lines = [];
      const rows = table.querySelectorAll("tr");
      rows.forEach(function (row) {
        const cells = Array.from(row.children).map(function (cell) {
          const raw = cell.dataset.value != null && cell.dataset.value !== "" ? cell.dataset.value : cell.textContent;
          return '"' + String(raw).replaceAll('"', '""') + '"';
        });
        lines.push(cells.join(","));
      });
      const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "trades.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });
  }

  // ===== Full Sweep (Start button) =====
  function esc(str) {
    return String(str == null ? "" : str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }
  function fmtMoney(v, signed) {
    const n = Number(v || 0);
    const abs = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (n < 0) return "-$" + abs;
    return (signed && n > 0 ? "+$" : "$") + abs;
  }
  function fmtPct(v, signed) {
    if (v == null) return "—";
    const n = Number(v);
    return (signed && n > 0 ? "+" : "") + n.toFixed(2) + "%";
  }
  function fmtPf(pf) {
    return pf == null ? "∞" : Number(pf).toFixed(2);
  }
  function fmtRatio(v) {
    return v == null ? "—" : Number(v).toFixed(2);
  }
  function tone(v) {
    if (v == null || v === 0) return "";
    return v > 0 ? "pos" : "neg";
  }

  const sweepPanel = document.getElementById("sweep-panel");
  const sweepMeta = document.getElementById("sweep-meta");
  const sweepActions = document.getElementById("sweep-actions");
  const sweepProgressWrap = document.getElementById("sweep-progress-wrap");
  const sweepStatusText = document.getElementById("sweep-status-text");
  const sweepCount = document.getElementById("sweep-count");
  const sweepBar = document.getElementById("sweep-progress-bar");
  const sweepError = document.getElementById("sweep-error");
  const sweepReportEl = document.getElementById("sweep-report");
  const copyBtn = document.getElementById("copy-report");
  const downloadBtn = document.getElementById("download-report");

  let activeMarkdown = "";
  let pollTimer = null;

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {
        fallbackCopy(text);
      });
    }
    fallbackCopy(text);
    return Promise.resolve();
  }
  function fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* noop */ }
    ta.remove();
  }

  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      if (!activeMarkdown) return;
      copyText(activeMarkdown).then(function () {
        const orig = copyBtn.textContent;
        copyBtn.textContent = "Copied!";
        setTimeout(function () { copyBtn.textContent = orig; }, 1800);
      });
    });
  }

  function renderReport(report, jobId) {
    const s = report.summary;
    const cfg = report.settings;
    activeMarkdown = report.report_md || "";
    sweepMeta.textContent =
      report.window_label +
      " · Starting balance $" + Number(cfg.balance).toLocaleString("en-US", { maximumFractionDigits: 2 }) +
      " · Commission " + cfg.commission + "%/side · Slippage " + cfg.slippage + "%" +
      (cfg.daily_filter ? " · Daily HAMA filter ON" : " · Daily HAMA filter OFF") +
      " · Completed in " + Number(s.elapsed_s).toFixed(1) + "s";

    downloadBtn.href = "/sweep/" + encodeURIComponent(jobId) + "/report.md";
    sweepActions.hidden = false;

    const overviewCards = [
      { label: "Setups evaluated", value: s.completed_combos + " / " + s.total_combos + " (" + s.active_combos + " active)", tone: "" },
      { label: "Profitable / Losing / Flat", value: s.profitable_combos + " / " + s.losing_combos + " / " + s.flat_combos, tone: "" },
      { label: "Combined Net P&L (all setups)", value: fmtMoney(s.total_net_profit, true), tone: tone(s.total_net_profit) },
      { label: "Total closed trades", value: Number(s.total_trades).toLocaleString("en-US"), tone: "" },
      { label: "Aggregate Win Rate", value: Number(s.overall_win_rate).toFixed(2) + "%", tone: s.overall_win_rate >= 50 ? "pos" : "neg" },
      { label: "Aggregate Profit Factor", value: fmtPf(s.overall_profit_factor), tone: (s.overall_profit_factor == null || s.overall_profit_factor > 1) ? "pos" : "neg" },
      { label: "Total commissions paid", value: fmtMoney(s.total_commission), tone: s.total_commission > 0 ? "neg" : "" },
      { label: "Best setup", value: s.best_combo ? (s.best_combo.venue_label + " · " + s.best_combo.strategy_label + " · " + s.best_combo.symbol_label + " (" + fmtMoney(s.best_combo.stats.net_profit, true) + ")") : "—", tone: s.best_combo ? tone(s.best_combo.stats.net_profit) : "" },
      { label: "Worst setup", value: s.worst_combo ? (s.worst_combo.venue_label + " · " + s.worst_combo.strategy_label + " · " + s.worst_combo.symbol_label + " (" + fmtMoney(s.worst_combo.stats.net_profit, true) + ")") : "—", tone: s.worst_combo ? tone(s.worst_combo.stats.net_profit) : "" },
    ];

    let html = '<h3>Executive Summary</h3><div class="metrics">';
    overviewCards.forEach(function (c) {
      html += '<div class="metric"><span>' + esc(c.label) + '</span><strong class="' + esc(c.tone) + '">' + esc(c.value) + "</strong></div>";
    });
    html += "</div>";

    html += '<h3>Automated Analysis</h3><div class="analysis-box"><span class="analysis-tag">Automated Analysis · Generated from Sweep Metrics</span><ul>';
    (report.analysis || []).forEach(function (line) {
      html += "<li>" + esc(line) + "</li>";
    });
    html += "</ul></div>";

    html += '<h3>Venue Aggregates</h3><div class="table-wrap short-table"><table class="left-1"><thead><tr>' +
      "<th>Venue</th><th>Combos</th><th>Active</th><th>Profitable</th><th>Trades (L/S)</th>" +
      "<th>Win Rate</th><th>Profit Factor</th><th>Net P&amp;L $</th><th>Avg Return %</th><th>Worst Combo DD</th><th>Fees</th>" +
      "</tr></thead><tbody>";
    (report.venue_aggregates || []).forEach(function (v) {
      html += "<tr>" +
        "<td>" + esc(v.venue_label) + "</td>" +
        "<td>" + v.combos + "</td>" +
        "<td>" + v.active + "</td>" +
        "<td>" + v.profitable + "</td>" +
        "<td>" + v.total_trades + " (" + v.long_trades + "L/" + v.short_trades + "S)</td>" +
        "<td>" + Number(v.win_rate).toFixed(2) + "%</td>" +
        "<td>" + esc(fmtPf(v.profit_factor)) + "</td>" +
        '<td class="' + tone(v.net_profit) + '">' + esc(fmtMoney(v.net_profit, true)) + "</td>" +
        '<td class="' + tone(v.avg_net_pct) + '">' + esc(fmtPct(v.avg_net_pct, true)) + "</td>" +
        '<td class="neg">' + esc(fmtMoney(-v.worst_dd)) + " (" + Number(v.worst_dd_pct).toFixed(2) + "%)</td>" +
        "<td>" + esc(fmtMoney(v.commission_paid)) + "</td>" +
        "</tr>";
    });
    html += "</tbody></table></div>";

    html += '<h3>Per-Strategy Aggregates</h3><div class="table-wrap short-table"><table class="left-2" id="sweep-strat-agg"><thead><tr>' +
      '<th data-type="text">Venue</th><th data-type="text">Strategy</th><th data-type="number">Coins</th><th data-type="number">Active</th><th data-type="number">Profitable</th>' +
      '<th data-type="number">Trades</th><th data-type="text">L / S</th><th data-type="number">Win Rate</th><th data-type="number">PF</th>' +
      '<th data-type="number">Total Net P&amp;L</th><th data-type="number">Avg Net %</th><th data-type="number">Expectancy/trade</th><th data-type="number">Worst DD %</th><th data-type="number">Avg Time in Mkt</th>' +
      "</tr></thead><tbody>";
    (report.strategy_aggregates || []).forEach(function (st) {
      const tim = st.avg_time_in_market_pct == null ? "—" : Number(st.avg_time_in_market_pct).toFixed(1) + "%";
      const pfVal = st.profit_factor == null ? 999999 : st.profit_factor;
      html += "<tr>" +
        '<td data-value="' + esc(st.venue_label) + '">' + esc(st.venue_label) + "</td>" +
        '<td data-value="' + esc(st.strategy_label) + '">' + esc(st.strategy_label) + "</td>" +
        '<td data-value="' + st.coins_tested + '">' + st.coins_tested + "</td>" +
        '<td data-value="' + st.active_coins + '">' + st.active_coins + "</td>" +
        '<td data-value="' + st.profitable_coins + '">' + st.profitable_coins + "</td>" +
        '<td data-value="' + st.total_trades + '">' + st.total_trades + "</td>" +
        '<td data-value="' + st.long_trades + '">' + st.long_trades + "L / " + st.short_trades + "S</td>" +
        '<td data-value="' + st.win_rate + '">' + Number(st.win_rate).toFixed(2) + "%</td>" +
        '<td data-value="' + pfVal + '">' + esc(fmtPf(st.profit_factor)) + "</td>" +
        '<td data-value="' + st.total_net_profit + '" class="' + tone(st.total_net_profit) + '">' + esc(fmtMoney(st.total_net_profit, true)) + "</td>" +
        '<td data-value="' + st.avg_net_pct + '" class="' + tone(st.avg_net_pct) + '">' + esc(fmtPct(st.avg_net_pct, true)) + "</td>" +
        '<td data-value="' + st.expectancy + '" class="' + tone(st.expectancy) + '">' + esc(fmtMoney(st.expectancy, true)) + "</td>" +
        '<td data-value="' + st.worst_dd_pct + '" class="neg">' + esc(fmtMoney(-st.worst_dd)) + " (" + Number(st.worst_dd_pct).toFixed(2) + "%)</td>" +
        '<td data-value="' + (st.avg_time_in_market_pct == null ? -1 : st.avg_time_in_market_pct) + '">' + esc(tim) + "</td>" +
        "</tr>";
    });
    html += "</tbody></table></div>";

    html += '<div class="split-grid" style="margin-top:16px">' +
      '<div class="card-box"><h4>Best 5 Setups (by Net P&amp;L)</h4><ol>';
    (report.best_5 || []).forEach(function (r) {
      const st = r.stats;
      html += "<li><strong>" + esc(r.venue_label + " · " + r.strategy_label + " · " + r.symbol_label) + "</strong>: " +
        '<span class="' + tone(st.net_profit) + '">' + esc(fmtMoney(st.net_profit, true)) + " (" + esc(fmtPct(st.net_profit_pct, true)) + ")</span> · " +
        st.total_trades + " trades · WR " + Number(st.win_rate).toFixed(1) + "% · PF " + esc(fmtPf(st.profit_factor)) +
        " · DD " + Number(st.max_drawdown_pct).toFixed(2) + "%</li>";
    });
    html += "</ol></div><div class='card-box'><h4>Worst 5 Setups (by Net P&amp;L)</h4><ol>";
    (report.worst_5 || []).forEach(function (r) {
      const st = r.stats;
      html += "<li><strong>" + esc(r.venue_label + " · " + r.strategy_label + " · " + r.symbol_label) + "</strong>: " +
        '<span class="' + tone(st.net_profit) + '">' + esc(fmtMoney(st.net_profit, true)) + " (" + esc(fmtPct(st.net_profit_pct, true)) + ")</span> · " +
        st.total_trades + " trades · WR " + Number(st.win_rate).toFixed(1) + "% · PF " + esc(fmtPf(st.profit_factor)) +
        " · DD " + Number(st.max_drawdown_pct).toFixed(2) + "%</li>";
    });
    html += "</ol></div></div>";

    html += '<div class="panel-head"><h3>All Setups Ranked</h3><p class="muted">' +
      (report.ranked || []).length + ' combinations · click any column header to sort</p></div>' +
      '<div class="table-wrap"><table id="sweep-ranked" class="left-ranked"><thead><tr>' +
      '<th data-type="number">#</th><th data-type="text">Venue</th><th data-type="text">Strategy</th><th data-type="text">Coin</th>' +
      '<th data-type="number">Net P&amp;L $</th><th data-type="number">Net %</th><th data-type="number">PF</th>' +
      '<th data-type="number">Max DD $</th><th data-type="number">Max DD %</th><th data-type="number">Ret/DD</th>' +
      '<th data-type="number">Trades</th><th data-type="number">Win Rate</th><th data-type="number">Expectancy</th>' +
      '<th data-type="number">Avg Win</th><th data-type="number">Avg Loss</th><th data-type="number">W/L Ratio</th>' +
      '<th data-type="number">Best Win</th><th data-type="number">Worst Loss</th><th data-type="text">Max Consec W/L</th>' +
      '<th data-type="number">Sharpe</th><th data-type="number">Long P&amp;L</th><th data-type="number">Short P&amp;L</th>' +
      '<th data-type="number">Time in Mkt</th><th data-type="number">Avg Dur</th><th data-type="number">Fees</th><th data-type="text">Exit Reasons</th>' +
      "</tr></thead><tbody>";

    (report.ranked || []).forEach(function (r) {
      if (r.error || !r.stats) {
        html += "<tr><td>" + r.rank + "</td><td>" + esc(r.venue_label) + "</td><td>" + esc(r.strategy_label) +
          "</td><td>" + esc(r.symbol_label) + '</td><td colspan="22" class="neg">Error: ' + esc(r.error) + "</td></tr>";
        return;
      }
      const st = r.stats;
      const pfVal = st.profit_factor == null ? 999999 : st.profit_factor;
      const tim = st.time_in_market_pct == null ? "—" : Number(st.time_in_market_pct).toFixed(1) + "%";
      const reasons = Object.keys(st.exit_reasons || {}).sort().map(function (k) { return k + ":" + st.exit_reasons[k]; }).join(", ") || "—";
      html += "<tr>" +
        '<td data-value="' + r.rank + '">' + r.rank + "</td>" +
        '<td data-value="' + esc(r.venue_label) + '">' + esc(r.venue_label) + "</td>" +
        '<td data-value="' + esc(r.strategy_label) + '">' + esc(r.strategy_label) + "</td>" +
        '<td data-value="' + esc(r.symbol_label) + '">' + esc(r.symbol_label) + "</td>" +
        '<td data-value="' + st.net_profit + '" class="' + tone(st.net_profit) + '">' + esc(fmtMoney(st.net_profit, true)) + "</td>" +
        '<td data-value="' + st.net_profit_pct + '" class="' + tone(st.net_profit_pct) + '">' + esc(fmtPct(st.net_profit_pct, true)) + "</td>" +
        '<td data-value="' + pfVal + '">' + esc(fmtPf(st.profit_factor)) + "</td>" +
        '<td data-value="' + (-st.max_drawdown) + '" class="' + (st.max_drawdown > 0 ? "neg" : "") + '">' + esc(fmtMoney(-st.max_drawdown)) + "</td>" +
        '<td data-value="' + st.max_drawdown_pct + '" class="' + (st.max_drawdown_pct > 0 ? "neg" : "") + '">' + Number(st.max_drawdown_pct).toFixed(2) + "%</td>" +
        '<td data-value="' + (st.return_dd_ratio == null ? "" : st.return_dd_ratio) + '">' + esc(fmtRatio(st.return_dd_ratio)) + "</td>" +
        '<td data-value="' + st.total_trades + '">' + st.total_trades + " (" + st.long_trades + "L/" + st.short_trades + "S)</td>" +
        '<td data-value="' + st.win_rate + '">' + Number(st.win_rate).toFixed(2) + "%</td>" +
        '<td data-value="' + st.expectancy + '" class="' + tone(st.expectancy) + '">' + esc(fmtMoney(st.expectancy, true)) + "</td>" +
        '<td data-value="' + st.avg_win + '" class="pos">' + esc(fmtMoney(st.avg_win)) + "</td>" +
        '<td data-value="' + st.avg_loss + '" class="' + (st.avg_loss < 0 ? "neg" : "") + '">' + esc(fmtMoney(st.avg_loss)) + "</td>" +
        '<td data-value="' + (st.win_loss_ratio == null ? "" : st.win_loss_ratio) + '">' + esc(fmtRatio(st.win_loss_ratio)) + "</td>" +
        '<td data-value="' + st.largest_win + '" class="pos">' + esc(fmtMoney(st.largest_win)) + "</td>" +
        '<td data-value="' + st.largest_loss + '" class="' + (st.largest_loss < 0 ? "neg" : "") + '">' + esc(fmtMoney(st.largest_loss)) + "</td>" +
        '<td data-value="' + st.max_consec_wins + '">' + st.max_consec_wins + "W / " + st.max_consec_losses + "L</td>" +
        '<td data-value="' + (st.sharpe == null ? "" : st.sharpe) + '">' + esc(fmtRatio(st.sharpe)) + "</td>" +
        '<td data-value="' + st.long_net_profit + '" class="' + tone(st.long_net_profit) + '">' + esc(fmtMoney(st.long_net_profit, true)) + " (" + Number(st.long_win_rate).toFixed(0) + "%)</td>" +
        '<td data-value="' + st.short_net_profit + '" class="' + tone(st.short_net_profit) + '">' + esc(fmtMoney(st.short_net_profit, true)) + " (" + Number(st.short_win_rate).toFixed(0) + "%)</td>" +
        '<td data-value="' + (st.time_in_market_pct == null ? -1 : st.time_in_market_pct) + '">' + esc(tim) + "</td>" +
        '<td data-value="' + st.avg_duration_hours + '">' + Number(st.avg_duration_hours).toFixed(1) + "h</td>" +
        '<td data-value="' + st.commission_paid + '">' + esc(fmtMoney(st.commission_paid)) + "</td>" +
        '<td data-value="' + esc(reasons) + '">' + esc(reasons) + "</td>" +
        "</tr>";
    });
    html += "</tbody></table></div>";

    html += '<h3>Strategy Rules &amp; Full v3.1 Engine Specifications</h3><div class="spec-grid">';
    (report.strategy_specs || []).forEach(function (sp) {
      html += '<div class="spec-card"><h4>' + esc(sp.label) + ' <span class="muted">(' + esc(sp.id) + ")</span></h4><dl>" +
        "<dt>Coins &amp; Venues</dt><dd>" + esc(sp.coins_summary) + "</dd>" +
        "<dt>Signal TF</dt><dd>" + esc(sp.signal_tf) + "</dd>" +
        "<dt>Execution TF</dt><dd>" + esc(sp.execution_tf) + "</dd>" +
        "<dt>HTF / Macro Filter</dt><dd>" + esc(sp.htf_filter_tf) + "</dd>" +
        "<dt>Indicators</dt><dd>" + esc(sp.indicators) + "</dd>" +
        "<dt>Entry Rules</dt><dd>" + esc(sp.entry_rules) + "</dd>" +
        "<dt>Exit Rules</dt><dd>" + esc(sp.exit_rules) + "</dd>" +
        "<dt>TP &amp; SL</dt><dd>" + esc(sp.tp_sl) + "</dd>" +
        "<dt>Filters</dt><dd>" + esc(sp.filters) + "</dd>" +
        "<dt>Sizing &amp; Risk</dt><dd>" + esc(sp.sizing_and_risk) + "</dd>" +
        "<dt>Venue Diffs</dt><dd>" + esc(sp.venue_differences) + "</dd>" +
        "<dt>Fees / Slippage</dt><dd>" + esc(sp.fees_slippage) + "</dd>" +
        "</dl></div>";
    });
    html += "</div>";

    html += '<details class="rules md-preview"><summary>Full Markdown Report (AI-ready plain text — use Copy report or Download .md above)</summary><pre>' + esc(activeMarkdown) + "</pre></details>";

    sweepReportEl.innerHTML = html;
    sweepReportEl.hidden = false;
    makeSortable(document.getElementById("sweep-strat-agg"));
    makeSortable(document.getElementById("sweep-ranked"));
  }

  function pollJob(jobId) {
    fetch("/sweep/" + encodeURIComponent(jobId))
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          throw new Error((res.data && res.data.error) || "Sweep status failed");
        }
        const d = res.data;
        const pct = d.total > 0 ? Math.round((d.done / d.total) * 100) : 0;
        sweepCount.textContent = d.done + " / " + d.total + " (" + pct + "%) · " + Number(d.elapsed_s || 0).toFixed(1) + "s";
        sweepStatusText.textContent = d.current || "Running…";
        sweepBar.style.width = pct + "%";

        if (d.status === "done" && d.report) {
          clearInterval(pollTimer);
          pollTimer = null;
          sweepProgressWrap.hidden = true;
          if (startBtn) { startBtn.disabled = false; startBtn.textContent = "Start"; }
          if (run) run.disabled = false;
          if (rankBtn) rankBtn.disabled = false;
          renderReport(d.report, jobId);
        } else if (d.status === "error" || d.status === "cancelled") {
          clearInterval(pollTimer);
          pollTimer = null;
          if (startBtn) { startBtn.disabled = false; startBtn.textContent = "Start"; }
          if (run) run.disabled = false;
          if (rankBtn) rankBtn.disabled = false;
          sweepError.textContent = d.error || "Sweep stopped (" + d.status + ").";
          sweepError.hidden = false;
        }
      })
      .catch(function (err) {
        clearInterval(pollTimer);
        pollTimer = null;
        if (startBtn) { startBtn.disabled = false; startBtn.textContent = "Start"; }
        if (run) run.disabled = false;
          if (rankBtn) rankBtn.disabled = false;
        sweepError.textContent = String(err && err.message ? err.message : err);
        sweepError.hidden = false;
      });
  }

  if (startBtn && startBtn.form) {
    startBtn.addEventListener("click", function () {
      if (!startBtn.form.reportValidity()) return;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      const balanceEl = document.getElementById("balance");
      const commEl = document.getElementById("commission");
      const slipEl = document.getElementById("slippage");
      const dailyEl = document.getElementById("daily_hama");

      const emptyState = document.getElementById("empty-state");
      if (emptyState) emptyState.hidden = true;

      sweepPanel.hidden = false;
      sweepProgressWrap.hidden = false;
      sweepActions.hidden = true;
      sweepReportEl.hidden = true;
      sweepReportEl.innerHTML = "";
      sweepError.hidden = true;
      sweepError.textContent = "";
      sweepBar.style.width = "0%";
      sweepCount.textContent = "0 / 70";
      sweepStatusText.textContent = "Starting full analysis…";
      sweepMeta.textContent = "Running every enabled Pivex and Binance strategy/coin combo sequentially…";

      startBtn.disabled = true;
      startBtn.textContent = "Analyzing…";
      if (run) run.disabled = true;
      if (rankBtn) rankBtn.disabled = true;

      fetch("/sweep/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          balance: balanceEl ? balanceEl.value : "10000",
          commission: commEl ? commEl.value : "0.04",
          slippage: slipEl ? slipEl.value : "0.0",
          daily_hama: dailyEl ? dailyEl.checked : true,
        }),
      })
        .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) throw new Error((res.data && res.data.error) || "Failed to start sweep");
          const jobId = res.data.job_id;
          sweepCount.textContent = "0 / " + res.data.total;
          pollTimer = setInterval(function () { pollJob(jobId); }, 1000);
          pollJob(jobId);
        })
        .catch(function (err) {
          if (startBtn) { startBtn.disabled = false; startBtn.textContent = "Start"; }
          if (run) run.disabled = false;
          if (rankBtn) rankBtn.disabled = false;
          sweepError.textContent = String(err && err.message ? err.message : err);
          sweepError.hidden = false;
        });
    });
  }


  // ===== Tabs + Ranking =====
  const tabSingle = document.getElementById("tab-single");
  const tabRanking = document.getElementById("tab-ranking");
  const singlePane = document.getElementById("single-pane");
  const rankingPanel = document.getElementById("ranking-panel");
  const rankingMeta = document.getElementById("ranking-meta");
  const rankingProgressWrap = document.getElementById("ranking-progress-wrap");
  const rankingStatusText = document.getElementById("ranking-status-text");
  const rankingCount = document.getElementById("ranking-count");
  const rankingBar = document.getElementById("ranking-progress-bar");
  const rankingError = document.getElementById("ranking-error");
  const rankingReportEl = document.getElementById("ranking-report");
  let rankingTimer = null;

  function showTab(name) {
    const isRank = name === "ranking";
    if (tabSingle) {
      tabSingle.classList.toggle("active", !isRank);
      tabSingle.setAttribute("aria-selected", isRank ? "false" : "true");
    }
    if (tabRanking) {
      tabRanking.classList.toggle("active", isRank);
      tabRanking.setAttribute("aria-selected", isRank ? "true" : "false");
    }
    if (singlePane) singlePane.hidden = isRank;
    if (rankingPanel) rankingPanel.hidden = !isRank;
  }
  if (tabSingle) tabSingle.addEventListener("click", function () { showTab("single"); });
  if (tabRanking) tabRanking.addEventListener("click", function () { showTab("ranking"); loadLatestRanking(); });

  function setBusy(busy, rankLabel) {
    if (rankBtn) {
      rankBtn.disabled = busy;
      rankBtn.textContent = busy ? (rankLabel || "Ranking…") : "Rank";
    }
    if (startBtn) startBtn.disabled = busy;
    if (run) run.disabled = busy;
  }

  function renderRanking(report) {
    const s = report.summary || {};
    const cfg = report.settings || {};
    const uni = report.universe || {};
    if (rankingMeta) {
      rankingMeta.textContent =
        (report.source_label ? report.source_label + " · " : "") +
        (report.window_label || "1-year Ranking window") +
        " · Starting balance $" + Number(cfg.balance || 0).toLocaleString("en-US", { maximumFractionDigits: 2 }) +
        " · Commission " + cfg.commission + "%/side · Slippage " + cfg.slippage + "%" +
        (cfg.daily_filter ? " · Daily HAMA filter ON" : " · Daily HAMA filter OFF") +
        " · " + (s.strategies_ranked || 0) + " strategy rows in " + Number(s.elapsed_s || 0).toFixed(1) + "s. " +
        (cfg.native_timeframes || "Each strategy uses its own native timeframe.");
    }

    function rowCells(r, includeTested) {
      if (r.error || !r.stats) {
        return "<tr><td>" + (r.rank || "") + "</td><td>" + esc(r.strategy_label) +
          "</td><td>" + esc(r.venue_label) + "</td><td>" + esc(r.symbol_label) +
          '</td><td colspan="16" class="neg">Error: ' + esc(r.error || "No stats") + "</td></tr>";
      }
      const st = r.stats;
      const pfVal = st.profit_factor == null ? 999999 : st.profit_factor;
      const tested = includeTested
        ? '<td data-value="' + (r.symbols_tested || 0) + '">' + (r.completed_symbols || 0) + " / " + (r.symbols_tested || 0) + "</td>"
        : "";
      const suspect = !!(r.suspect || (r.strategy_id === "ml_5m" && r.venue === "binance" && r.symbol === "BIGTIMEUSDT"));
      const suspectTag = suspect ? '<span class="suspect-tag">Suspect — tester bug, ignore</span>' : "";
      return "<tr" + (suspect ? ' class="suspect"' : "") + ">" +
        '<td data-value="' + r.rank + '">' + r.rank + "</td>" +
        '<td data-value="' + esc(r.strategy_label) + '">' + esc(r.strategy_label) + suspectTag + "</td>" +
        '<td data-value="' + esc(r.venue_label) + '">' + esc(r.venue_label) + "</td>" +
        '<td data-value="' + esc(r.symbol_label) + '">' + esc(r.symbol_label) + "</td>" +
        tested +
        '<td data-value="' + esc(r.signal_tf || "") + '" class="rank-text">' + esc(r.signal_tf || "—") + "</td>" +
        '<td data-value="' + st.net_profit + '" class="' + tone(st.net_profit) + '">' + esc(fmtMoney(st.net_profit, true)) + "</td>" +
        '<td data-value="' + st.net_profit_pct + '" class="' + tone(st.net_profit_pct) + '">' + esc(fmtPct(st.net_profit_pct, true)) + "</td>" +
        '<td data-value="' + pfVal + '">' + esc(fmtPf(st.profit_factor)) + "</td>" +
        '<td data-value="' + (-st.max_drawdown) + '" class="' + (st.max_drawdown > 0 ? "neg" : "") + '">' + esc(fmtMoney(-st.max_drawdown)) + "</td>" +
        '<td data-value="' + st.max_drawdown_pct + '" class="' + (st.max_drawdown_pct > 0 ? "neg" : "") + '">' + Number(st.max_drawdown_pct).toFixed(2) + "%</td>" +
        '<td data-value="' + st.win_rate + '">' + Number(st.win_rate).toFixed(2) + "%</td>" +
        '<td data-value="' + st.total_trades + '">' + st.total_trades + "</td>" +
        '<td data-value="' + st.avg_win + '" class="pos">' + esc(fmtMoney(st.avg_win)) + "</td>" +
        '<td data-value="' + st.avg_loss + '" class="' + (st.avg_loss < 0 ? "neg" : "") + '">' + esc(fmtMoney(st.avg_loss)) + "</td>" +
        '<td data-value="' + st.expectancy + '" class="' + tone(st.expectancy) + '">' + esc(fmtMoney(st.expectancy, true)) + "</td>" +
        '<td data-value="' + (st.win_loss_ratio == null ? "" : st.win_loss_ratio) + '">' + esc(fmtRatio(st.win_loss_ratio)) + "</td>" +
        '<td data-value="' + st.commission_paid + '">' + esc(fmtMoney(st.commission_paid)) + "</td>" +
        '<td data-value="' + esc(r.tp || "") + '" class="rank-text">' + esc(r.tp || "—") + "</td>" +
        '<td data-value="' + esc(r.sl || "") + '" class="rank-text">' + esc(r.sl || "—") + "</td>" +
        '<td data-value="' + esc(r.indicators || "") + '" class="rank-text">' + esc(r.indicators || "—") + "</td>" +
        "</tr>";
    }

    const head = "<th data-type=\"number\">#</th>" +
      "<th data-type=\"text\">Strategy</th><th data-type=\"text\">Venue</th><th data-type=\"text\">Best symbol</th>" +
      "<th data-type=\"number\">Symbols tested</th><th data-type=\"text\" class=\"rank-text\">Native TF</th>" +
      "<th data-type=\"number\">Net $</th><th data-type=\"number\">Net %</th><th data-type=\"number\">PF</th>" +
      "<th data-type=\"number\">Max DD $</th><th data-type=\"number\">Max DD %</th>" +
      "<th data-type=\"number\">Win rate</th><th data-type=\"number\">Trades</th>" +
      "<th data-type=\"number\">Avg win</th><th data-type=\"number\">Avg loss</th>" +
      "<th data-type=\"number\">Expectancy</th><th data-type=\"number\">Avg R</th>" +
      "<th data-type=\"number\">Commission</th>" +
      "<th data-type=\"text\" class=\"rank-text\">TP</th><th data-type=\"text\" class=\"rank-text\">SL</th>" +
      "<th data-type=\"text\" class=\"rank-text\">Indicators</th>";

    let html = '<p class="muted">' + esc(report.source_label || "") + "</p>";
    html += '<p class="muted">' + esc(uni.binance_source || "") + " " + esc(uni.pivex_note || "") +
      " Best symbol is the highest net profit in the 1-year window, then PF, then trade count. " +
      "This window includes the former 2026-07-01 to 2026-10-25 holdout. Click a column header to sort. Default order is PF, then net return. " +
      "A fresh Rank press on the live free plan may take too long for a full year; this tab loads the precomputed 1-year results.</p>";
    const warnings = report.suspect_warnings || [];
    if (!warnings.length && (report.ranked || []).some(function (r) { return r.suspect || (r.strategy_id === "ml_5m" && r.venue === "binance" && r.symbol === "BIGTIMEUSDT"); })) {
      warnings.push({
        strategy_label: "ML Lorentz · 5m",
        venue_label: "Binance",
        symbol_label: "BIGTIME",
        reason: "Tester artifact, not a real result. All exits are labeled stop yet counted as wins, with an implausible average win versus ML's 3% stop and 3.1% target. Ignore this row."
      });
    }
    warnings.forEach(function (w) {
      html += '<div class="banner warn suspect-banner" role="status"><strong>Suspect row: ' +
        esc((w.venue_label || "") + " · " + (w.strategy_label || "") + " · " + (w.symbol_label || "")) +
        "</strong> — " + esc(w.reason || "") + "</div>";
    });

    const mh = report.morning_hama;
    if (mh) {
      const st = mh.stats;
      html += '<h3>' + esc(mh.label || "SOL 1m Morning HAMA — $1,000 target test") + '</h3>';
      html += '<div class="analysis-box"><span class="analysis-tag">Test variant · official engine still has no target</span>';
      html += '<p class="muted">' + esc(mh.note || "") + '</p>';
      if (st) {
        html += '<div class="metrics">' +
          '<div class="metric"><span>Net profit</span><strong class="' + tone(st.net_profit) + '">' + esc(fmtMoney(st.net_profit, true)) + " (" + esc(fmtPct(st.net_profit_pct, true)) + ")</strong></div>" +
          '<div class="metric"><span>Profit factor</span><strong>' + esc(fmtPf(st.profit_factor)) + "</strong></div>" +
          '<div class="metric"><span>Max drawdown</span><strong class="' + (st.max_drawdown > 0 ? "neg" : "") + '">' + esc(fmtMoney(-st.max_drawdown)) + " (" + Number(st.max_drawdown_pct).toFixed(2) + "%)</strong></div>" +
          '<div class="metric"><span>Win rate</span><strong>' + Number(st.win_rate).toFixed(2) + "%</strong></div>" +
          '<div class="metric"><span>Trades</span><strong>' + st.total_trades + "</strong></div>" +
          '<div class="metric"><span>Venue / symbol / TF</span><strong>' + esc((mh.venue_label || "Pivex") + " · " + (mh.symbol_label || "SOL") + " · " + (mh.timeframe || "1m")) + "</strong></div>" +
          "</div>";
      } else if (mh.row && mh.row.error) {
        html += '<p class="neg">Error: ' + esc(mh.row.error) + "</p>";
      } else {
        html += '<p class="muted">No Morning HAMA result in this ranking run.</p>';
      }
      html += "</div>";
    }
    html += '<div class="table-wrap"><table id="ranking-table" class="left-ranked"><thead><tr>' + head +
      "</tr></thead><tbody>";
    (report.ranked || []).forEach(function (r) { html += rowCells(r, true); });
    html += "</tbody></table></div>";

    if (report.all_results && report.all_results.length) {
      const allHead = head.replace("Best symbol", "Symbol").replace("<th data-type=\"number\">Symbols tested</th>", "");
      html += '<details class="rules"><summary>All ' + report.all_results.length +
        " tested combos (not just the best symbol)</summary><div class=\"table-wrap\"><table id=\"ranking-all\" class=\"left-ranked\"><thead><tr>" +
        allHead + "</tr></thead><tbody>";
      (report.all_results || []).forEach(function (r, i) {
        r.rank = r.rank || (i + 1);
        html += rowCells(r, false);
      });
      html += "</tbody></table></div></details>";
    }

    rankingReportEl.innerHTML = html;
    rankingReportEl.hidden = false;
    makeSortable(document.getElementById("ranking-table"));
    makeSortable(document.getElementById("ranking-all"));
  }

  function pollRanking(jobId) {
    fetch("/ranking/" + encodeURIComponent(jobId))
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          try { sessionStorage.removeItem("rankingJobId"); } catch (e) { /* noop */ }
          throw new Error((res.data && res.data.error) || "Ranking status failed");
        }
        const d = res.data;
        const pct = d.total > 0 ? Math.round((d.done / d.total) * 100) : 0;
        if (rankingCount) rankingCount.textContent = d.done + " / " + d.total + " (" + pct + "%) · " + Number(d.elapsed_s || 0).toFixed(1) + "s";
        if (rankingStatusText) rankingStatusText.textContent = d.current || "Running…";
        if (rankingBar) rankingBar.style.width = pct + "%";
        if (d.status === "done" && d.report) {
          clearInterval(rankingTimer);
          rankingTimer = null;
          try { sessionStorage.removeItem("rankingJobId"); } catch (e) { /* noop */ }
          if (rankingProgressWrap) rankingProgressWrap.hidden = true;
          setBusy(false);
          renderRanking(d.report);
        } else if (d.status === "error" || d.status === "cancelled") {
          clearInterval(rankingTimer);
          rankingTimer = null;
          try { sessionStorage.removeItem("rankingJobId"); } catch (e) { /* noop */ }
          setBusy(false);
          if (rankingError) {
            rankingError.textContent = d.error || "Ranking stopped (" + d.status + ").";
            rankingError.hidden = false;
          }
        }
      })
      .catch(function (err) {
        clearInterval(rankingTimer);
        rankingTimer = null;
        setBusy(false);
        if (rankingError) {
          rankingError.textContent = String(err && err.message ? err.message : err);
          rankingError.hidden = false;
        }
      });
  }

  function startRankingPoll(jobId, total) {
    if (rankingTimer) { clearInterval(rankingTimer); rankingTimer = null; }
    if (rankingCount) rankingCount.textContent = "0 / " + (total || 472);
    rankingTimer = setInterval(function () { pollRanking(jobId); }, 1500);
    pollRanking(jobId);
  }

  let latestLoaded = false;
  function showLoading() {
    if (rankingReportEl && rankingReportEl.hidden) {
      rankingReportEl.hidden = false;
      rankingReportEl.innerHTML = '<p class="muted">Loading ranking…</p>';
    }
  }
  function applyLatest(d) {
    if (!d) return;
    if (d.status === "queued" || d.status === "running") {
      if (rankingProgressWrap) rankingProgressWrap.hidden = false;
      const pct = d.total > 0 ? Math.round((d.done / d.total) * 100) : 0;
      if (rankingCount) rankingCount.textContent = (d.done || 0) + " / " + (d.total || 472) + " (" + pct + "%) · " + Number(d.elapsed_s || 0).toFixed(1) + "s";
      if (rankingStatusText) rankingStatusText.textContent = d.current || "Running…";
      if (rankingBar) rankingBar.style.width = pct + "%";
      if (d.job_id) startRankingPoll(d.job_id, d.total);
      return;
    }
    if (d.report) {
      if (rankingProgressWrap) rankingProgressWrap.hidden = true;
      renderRanking(d.report);
      latestLoaded = true;
    }
  }
  function loadLatestRanking() {
    if (rankingTimer) return;
    if (latestLoaded && rankingReportEl && !rankingReportEl.hidden) return;
    showLoading();
    fetch("/ranking/latest")
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error((res.data && res.data.error) || "Failed to load ranking");
        applyLatest(res.data);
      })
      .catch(function (err) {
        if (rankingError) {
          rankingError.textContent = String(err && err.message ? err.message : err);
          rankingError.hidden = false;
        }
      });
  }

  if (rankBtn) {
    rankBtn.addEventListener("click", function () {
      const form = rankBtn.form || document.querySelector("form.controls");
      if (form && form.reportValidity && !form.reportValidity()) return;
      showTab("ranking");
      const balanceEl = document.getElementById("balance");
      const commEl = document.getElementById("commission");
      const slipEl = document.getElementById("slippage");
      const dailyEl = document.getElementById("daily_hama");
      if (rankingProgressWrap) rankingProgressWrap.hidden = false;
      if (rankingReportEl) { rankingReportEl.hidden = true; rankingReportEl.innerHTML = ""; }
      if (rankingError) { rankingError.hidden = true; rankingError.textContent = ""; }
      if (rankingBar) rankingBar.style.width = "0%";
      if (rankingCount) rankingCount.textContent = "0 / 472";
      if (rankingStatusText) rankingStatusText.textContent = "Starting ranking…";
      if (rankingMeta) rankingMeta.textContent = "Testing every enabled strategy on 38 Binance USDT-M symbols and 24 Pivex crypto pairs. Each strategy stays on its own native timeframe. This runs in the background and can take a while the first time symbols are downloaded.";
      setBusy(true, "Ranking…");
      fetch("/ranking/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          balance: balanceEl ? balanceEl.value : "10000",
          commission: commEl ? commEl.value : "0.04",
          slippage: slipEl ? slipEl.value : "0.0",
          daily_hama: dailyEl ? dailyEl.checked : true,
        }),
      })
        .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) throw new Error((res.data && res.data.error) || "Failed to start ranking");
          try { sessionStorage.setItem("rankingJobId", res.data.job_id); } catch (e) { /* noop */ }
          startRankingPoll(res.data.job_id, res.data.total);
        })
        .catch(function (err) {
          setBusy(false);
          if (rankingError) {
            rankingError.textContent = String(err && err.message ? err.message : err);
            rankingError.hidden = false;
          }
        });
    });
  }

  try {
    const existing = sessionStorage.getItem("rankingJobId");
    if (existing) {
      fetch("/ranking/" + encodeURIComponent(existing))
        .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
        .then(function (res) {
          if (res.ok && res.data && (res.data.status === "queued" || res.data.status === "running")) {
            startRankingPoll(existing, res.data.total);
            return;
          }
          if (res.ok && res.data && res.data.status === "done" && res.data.report) {
            try { sessionStorage.removeItem("rankingJobId"); } catch (e2) { /* noop */ }
            renderRanking(res.data.report);
            latestLoaded = true;
            return;
          }
          try { sessionStorage.removeItem("rankingJobId"); } catch (e2) { /* noop */ }
          loadLatestRanking();
        })
        .catch(function () {
          try { sessionStorage.removeItem("rankingJobId"); } catch (e2) { /* noop */ }
          loadLatestRanking();
        });
    } else {
      loadLatestRanking();
    }
  } catch (e) {
    loadLatestRanking();
  }


  const payload = document.getElementById("chart-data");
  if (!payload || typeof LightweightCharts === "undefined") return;
  const data = JSON.parse(payload.textContent);
  const common = {
    autoSize: true,
    layout: { background: { color: "#131722" }, textColor: "#d1d4dc", attributionLogo: false },
    grid: { vertLines: { color: "#2a2e39" }, horzLines: { color: "#2a2e39" } },
    rightPriceScale: { borderColor: "#2a2e39" },
    timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  };

  function mount(el, build) {
    function go() {
      if (el.clientWidth < 20) {
        requestAnimationFrame(go);
        return;
      }
      build();
    }
    go();
  }

  const equityEl = document.getElementById("equity-chart");
  if (equityEl && data.equity && data.equity.length) {
    mount(equityEl, function () {
    const equityChart = LightweightCharts.createChart(equityEl, common);
    const area = equityChart.addAreaSeries({
      lineColor: "#2962ff",
      topColor: "rgba(41, 98, 255, 0.35)",
      bottomColor: "rgba(41, 98, 255, 0.02)",
      priceLineVisible: false,
      priceScaleId: "right",
    });
    area.setData(data.equity);
    const dd = equityChart.addHistogramSeries({
      priceScaleId: "dd",
      color: "rgba(239, 83, 80, 0.45)",
      priceLineVisible: false,
      priceFormat: { type: "percent", precision: 2, minMove: 0.01 },
    });
    dd.setData(data.drawdown.map(function (point) {
      return { time: point.time, value: point.value / 100, color: "rgba(239, 83, 80, 0.45)" };
    }));
    equityChart.priceScale("dd").applyOptions({ scaleMargins: { top: 0.72, bottom: 0 } });
    equityChart.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.28 } });
    equityChart.timeScale().fitContent();
    });
  }

  const priceEl = document.getElementById("price-chart");
  if (priceEl && data.candles && data.candles.length) {
    mount(priceEl, function () {
    const priceChart = LightweightCharts.createChart(priceEl, common);
    const candles = priceChart.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderUpColor: "#26a69a",
      borderDownColor: "#ef5350",
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    candles.setData(data.candles);
    if (data.markers && data.markers.length) {
      candles.setMarkers(data.markers);
    }
    priceChart.timeScale().fitContent();
    });
  }
})();
