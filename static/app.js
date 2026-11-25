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
      Array.from(venueSelect.options).forEach(function (option) {
        const venue = spec.venues[option.value];
        option.disabled = !venue || !venue.enabled;
      });
      if (venueSelect.selectedOptions[0] && venueSelect.selectedOptions[0].disabled) {
        const fallback = Array.from(venueSelect.options).find(function (option) { return !option.disabled; });
        if (fallback) venueSelect.value = fallback.value;
      }
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
  if (run && run.form) {
    run.form.addEventListener("submit", function () {
      run.disabled = true;
      run.textContent = "Running…";
    });
  }

  const table = document.getElementById("trades");
  if (table) {
    const headers = Array.from(table.querySelectorAll("th"));
    headers.forEach(function (th, index) {
      th.addEventListener("click", function () {
        const type = th.dataset.type || "text";
        const body = table.tBodies[0];
        const rows = Array.from(body.rows);
        const dir = th.dataset.dir === "asc" ? "desc" : "asc";
        headers.forEach(function (other) { other.dataset.dir = ""; });
        th.dataset.dir = dir;
        rows.sort(function (a, b) {
          const av = a.cells[index].dataset.value || "";
          const bv = b.cells[index].dataset.value || "";
          let cmp;
          if (type === "number") {
            cmp = parseFloat(av) - parseFloat(bv);
          } else {
            cmp = av < bv ? -1 : av > bv ? 1 : 0;
          }
          return dir === "asc" ? cmp : -cmp;
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  }

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
