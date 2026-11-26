"""Server-rendered futures strategy tester."""

from __future__ import annotations

import csv
import io
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request

from backtest.config import (
    DEFAULT_BALANCE,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_DAILY_FILTER,
    DEFAULT_K,
    DEFAULT_PERIOD,
    DEFAULT_SLIPPAGE_PCT,
    PERIODS,
    STRATEGY_BY_ID,
    TESTER_STRATEGIES,
    SYMBOLS,
    VENUE_LABELS,
    VENUE_ORDER,
    catalog,
    symbols_for,
    venue_enabled,
)
from backtest.ranking import annotate_suspect_rows, default_ranking_report, ranking_plan, run_ranking
from backtest.data import period_window
from backtest.runner import run_backtest
from backtest.strengths import coin_choices, run_strengths, strengths_plan, to_csv as strengths_csv
from backtest.sweep import combo_plan, run_sweep

app = Flask(__name__)
LAST: dict = {}

_SWEEP_LOCK = threading.Lock()
_SWEEP_RUN_LOCK = threading.Lock()
_SWEEP_JOBS: dict[str, dict] = {}
_RANK_JOBS: dict[str, dict] = {}
_RUN_JOBS: dict[str, dict] = {}
_STR_JOBS: dict[str, dict] = {}
_RUN_LOCK = threading.Lock()
_MAX_JOBS = 5
_MAX_RUN_JOBS = 3


def _money(value: float, signed: bool = False) -> str:
    if value < 0:
        return f"-${abs(value):,.2f}"
    text = f"${value:,.2f}"
    return f"+{text}" if signed and value > 0 else text


def _pct(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.2f}%"


def _tone(value: float | None) -> str:
    if value is None or value == 0:
        return ""
    return "pos" if value > 0 else "neg"


def _metrics(stats: dict) -> list[dict]:
    pf = stats["profit_factor"]
    ratio = stats["win_loss_ratio"]
    sharpe = stats["sharpe"]
    net = stats["net_profit"]
    dd = stats["max_drawdown"]
    return [
        {"label": "Net profit", "value": f"{_money(net, True)} ({_pct(stats['net_profit_pct'])})", "tone": _tone(net)},
        {"label": "PnL %", "value": _pct(stats["pnl_pct"]), "tone": _tone(stats["pnl_pct"])},
        {"label": "Gross profit", "value": _money(stats["gross_profit"]), "tone": "pos" if stats["gross_profit"] else ""},
        {"label": "Gross loss", "value": _money(stats["gross_loss"]), "tone": "neg" if stats["gross_loss"] else ""},
        {"label": "Profit factor", "value": "∞" if pf is None else f"{pf:.2f}", "tone": "pos" if (pf is None or pf > 1) else "neg" if pf < 1 else ""},
        {"label": "Max drawdown", "value": f"{_money(-dd)} ({stats['max_drawdown_pct']:.2f}%)", "tone": "neg" if dd else ""},
        {"label": "Total trades", "value": f"{stats['total_trades']:,}", "tone": ""},
        {"label": "Win rate", "value": f"{stats['win_rate']:.2f}%", "tone": "pos" if stats["win_rate"] >= 50 else "neg" if stats["total_trades"] else ""},
        {"label": "Sharpe", "value": "—" if sharpe is None else f"{sharpe:.2f}", "tone": _tone(sharpe)},
        {"label": "Avg trade", "value": _money(stats["avg_trade"], True), "tone": _tone(stats["avg_trade"])},
        {"label": "Avg winning trade", "value": _money(stats["avg_win"]), "tone": "pos" if stats["avg_win"] else ""},
        {"label": "Avg losing trade", "value": _money(stats["avg_loss"]), "tone": "neg" if stats["avg_loss"] else ""},
        {"label": "Ratio avg win / avg loss", "value": "—" if ratio is None else f"{ratio:.2f}", "tone": "pos" if ratio and ratio > 1 else ""},
        {"label": "Largest winning trade", "value": _money(stats["largest_win"]), "tone": "pos" if stats["largest_win"] else ""},
        {"label": "Largest losing trade", "value": _money(stats["largest_loss"]), "tone": "neg" if stats["largest_loss"] else ""},
        {"label": "Max consecutive wins", "value": str(stats["max_consec_wins"]), "tone": ""},
        {"label": "Max consecutive losses", "value": str(stats["max_consec_losses"]), "tone": ""},
        {"label": "Commission paid", "value": _money(stats["commission_paid"]), "tone": "neg" if stats["commission_paid"] else ""},
    ]


def _period(value) -> str:
    value = str(value or DEFAULT_PERIOD).strip().lower()
    return value if value in PERIODS else DEFAULT_PERIOD


def _form() -> dict:
    strategy = request.form.get("strategy", TESTER_STRATEGIES[0]["id"])
    if strategy not in STRATEGY_BY_ID:
        strategy = TESTER_STRATEGIES[0]["id"]
    venue = request.form.get("venue", "pivex")
    if venue not in VENUE_LABELS or not venue_enabled(STRATEGY_BY_ID[strategy], venue):
        venue = "pivex" if venue_enabled(STRATEGY_BY_ID[strategy], "pivex") else "binance"
    allowed = symbols_for(strategy, venue)
    symbol = request.form.get("symbol", allowed[0])
    if symbol not in allowed:
        symbol = allowed[0]
    if request.method == "POST":
        daily = request.form.get("daily_hama") == "1"
    else:
        daily = DEFAULT_DAILY_FILTER
    return {
        "strategy": strategy,
        "venue": venue,
        "symbol": symbol,
        "balance": request.form.get("balance", str(int(DEFAULT_BALANCE))) or str(int(DEFAULT_BALANCE)),
        "commission": request.form.get("commission", str(DEFAULT_COMMISSION_PCT)) or str(DEFAULT_COMMISSION_PCT),
        "slippage": request.form.get("slippage", str(DEFAULT_SLIPPAGE_PCT)) or str(DEFAULT_SLIPPAGE_PCT),
        "daily_hama": daily,
        "period": _period(request.form.get("period")),
    }


def _csv(result: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "#",
            "Type",
            "Entry time",
            "Entry price",
            "Exit time",
            "Exit price",
            "Signal",
            "Size",
            "PnL $",
            "PnL %",
            "Cumulative PnL",
            "Run-up",
            "Drawdown",
        ]
    )
    for trade in result["trades"]:
        writer.writerow(
            [
                trade["n"],
                trade["side"],
                trade["entry_time"],
                f"{trade['entry_price']:.8f}",
                trade["exit_time"],
                f"{trade['exit_price']:.8f}",
                trade["reason"],
                f"{trade['qty']:.8f}",
                f"{trade['pnl']:.4f}",
                f"{trade['pnl_pct']:.4f}",
                f"{trade['cumulative']:.4f}",
                f"{trade['run_up']:.4f}",
                f"{trade['drawdown']:.4f}",
            ]
        )
    return buffer.getvalue()


@app.template_filter("px")
def px(value: float) -> str:
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    if abs(number) >= 1:
        return f"{number:,.4f}"
    return f"{number:.6f}"


@app.template_filter("qty")
def qty(value: float) -> str:
    text = f"{float(value):.4f}"
    return text.rstrip("0").rstrip(".")


@app.template_filter("usd")
def usd(value: float) -> str:
    return _money(float(value), signed=True)


@app.template_filter("usd0")
def usd0(value: float) -> str:
    return _money(abs(float(value)), signed=False)


@app.template_filter("pct")
def pct(value: float) -> str:
    return _pct(float(value))


@app.route("/", methods=["GET", "POST"])
def index():
    form = _form()
    error = None
    result = None
    metrics = None
    posted_venue = request.form.get("venue", "pivex")
    spec = STRATEGY_BY_ID[form["strategy"]]
    if request.method == "POST" and not venue_enabled(spec, posted_venue):
        only = "Pivex" if venue_enabled(spec, "pivex") else "Binance"
        error = f"{spec['label']} runs on {only} only."
    elif request.method == "POST":
        try:
            balance = float(form["balance"])
            commission = float(form["commission"])
            slippage = float(form["slippage"])
            result = _single_run(form, balance, commission, slippage)
            metrics = _metrics(result["stats"])
            LAST["result"] = result
        except Exception as exc:  # noqa: BLE001 - show the failure on the page
            error = str(exc)
            traceback.print_exc()
    return _render(form, error, result, metrics)


def _single_run(form: dict, balance: float, commission: float, slippage: float) -> dict:
    result = run_backtest(
        form["strategy"],
        form["symbol"],
        balance,
        commission,
        slippage,
        window=period_window(form["period"]),
        venue=form["venue"],
        k=DEFAULT_K,
        daily_filter=bool(form["daily_hama"]),
    )
    result["period"] = form["period"]
    result["period_label"] = PERIODS[form["period"]]["label"]
    return result


def _render(form: dict, error, result, metrics):
    return render_template(
        "index.html",
        strategies=TESTER_STRATEGIES,
        periods=PERIODS,
        strength_coins=coin_choices(),
        symbols=SYMBOLS,
        symbol_choices=[(sid, SYMBOLS[sid]) for sid in symbols_for(form["strategy"], form["venue"])],
        venues=VENUE_ORDER,
        venue_labels=VENUE_LABELS,
        catalog=catalog(),
        form=form,
        error=error,
        result=result,
        metrics=metrics,
    )


def _trim_run_jobs() -> None:
    while len(_RUN_JOBS) > _MAX_RUN_JOBS:
        oldest = next(iter(_RUN_JOBS))
        if _RUN_JOBS[oldest]["status"] in ("queued", "running"):
            break
        _RUN_JOBS.pop(oldest, None)


@app.route("/run/start", methods=["POST"])
def run_start():
    """Single run as a background job, so a 1-year run does not hit a request timeout."""
    form = _form()
    spec = STRATEGY_BY_ID[form["strategy"]]
    posted_venue = request.form.get("venue", "pivex")
    if not venue_enabled(spec, posted_venue):
        only = "Pivex" if venue_enabled(spec, "pivex") else "Binance"
        return jsonify({"error": f"{spec['label']} runs on {only} only."}), 400
    try:
        balance = float(form["balance"])
        commission = float(form["commission"])
        slippage = float(form["slippage"])
        if balance <= 0:
            raise ValueError("Starting balance must be positive")
        if commission < 0 or slippage < 0:
            raise ValueError("Commission and slippage cannot be negative")
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    job_id = uuid.uuid4().hex[:12]
    label = f"{spec['label']} · {VENUE_LABELS[form['venue']]} · {form['symbol']} · {PERIODS[form['period']]['label']}"
    with _SWEEP_LOCK:
        _RUN_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "label": label,
            "form": form,
            "started_at": time.monotonic(),
            "elapsed_s": 0.0,
            "error": None,
            "result": None,
        }
        _trim_run_jobs()

    def _worker() -> None:
        with _RUN_LOCK:
            with _SWEEP_LOCK:
                job = _RUN_JOBS.get(job_id)
                if not job:
                    return
                job["status"] = "running"
                job["started_at"] = time.monotonic()
            try:
                result = _single_run(form, balance, commission, slippage)
                with _SWEEP_LOCK:
                    job = _RUN_JOBS.get(job_id)
                    if job:
                        job["result"] = result
                        job["status"] = "done"
                        job["elapsed_s"] = round(time.monotonic() - job["started_at"], 1)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                with _SWEEP_LOCK:
                    job = _RUN_JOBS.get(job_id)
                    if job:
                        job["status"] = "error"
                        job["error"] = str(exc)
                        job["elapsed_s"] = round(time.monotonic() - job["started_at"], 1)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"job_id": job_id, "label": label}), 202


@app.route("/run/<job_id>")
def run_status(job_id: str):
    with _SWEEP_LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Run job not found. The server may have restarted; press Run again."}), 404
        elapsed = job["elapsed_s"]
        if job["status"] in ("queued", "running"):
            elapsed = round(time.monotonic() - job["started_at"], 1)
        payload = {
            "job_id": job_id,
            "status": job["status"],
            "label": job["label"],
            "elapsed_s": elapsed,
            "error": job["error"],
        }
        result = job["result"]
    if result is not None:
        stats = result["stats"]
        payload["summary"] = {
            "strategy": result["strategy"],
            "symbol": result["symbol"],
            "venue": result["venue"],
            "period": result.get("period"),
            "window_label": result["window_label"],
            "total_trades": stats["total_trades"],
            "net_profit": stats["net_profit"],
            "profit_factor": stats["profit_factor"],
            "win_rate": stats["win_rate"],
            "max_drawdown_pct": stats["max_drawdown_pct"],
        }
        payload["view_url"] = f"/run/{job_id}/view"
    return jsonify(payload)


@app.route("/run/<job_id>/view")
def run_view(job_id: str):
    with _SWEEP_LOCK:
        job = _RUN_JOBS.get(job_id)
        result = job["result"] if job else None
        form = dict(job["form"]) if job else None
        error = job["error"] if job else "Run job not found. The server may have restarted; press Run again."
    if form is None:
        form = {
            "strategy": TESTER_STRATEGIES[0]["id"],
            "venue": "pivex",
            "symbol": "BTCUSDT",
            "balance": str(int(DEFAULT_BALANCE)),
            "commission": str(DEFAULT_COMMISSION_PCT),
            "slippage": str(DEFAULT_SLIPPAGE_PCT),
            "daily_hama": DEFAULT_DAILY_FILTER,
            "period": DEFAULT_PERIOD,
        }
    metrics = None
    if result is not None:
        metrics = _metrics(result["stats"])
        LAST["result"] = result
    elif not error:
        error = "This run has not finished yet."
    return _render(form, error, result, metrics)


@app.route("/strengths/start", methods=["POST"])
def strengths_start():
    """One run over every strategy on Pivex and Binance for one coin and period."""
    payload = request.get_json(silent=True) or request.form or {}
    try:
        symbol = str(payload.get("symbol", "BTCUSDT"))
        if symbol not in {c["id"] for c in coin_choices()}:
            raise ValueError(f"Unknown coin {symbol}")
        period = _period(payload.get("period"))
        balance = float(payload.get("balance", DEFAULT_BALANCE))
        commission = float(payload.get("commission", DEFAULT_COMMISSION_PCT))
        slippage = float(payload.get("slippage", DEFAULT_SLIPPAGE_PCT))
        raw_daily = payload.get("daily_hama", DEFAULT_DAILY_FILTER)
        daily_filter = raw_daily in (True, 1, "1", "true", "True")
        if balance <= 0:
            raise ValueError("Starting balance must be positive")
        if commission < 0 or slippage < 0:
            raise ValueError("Commission and slippage cannot be negative")
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    rows, skipped = strengths_plan(symbol)
    total = len(rows)
    job_id = uuid.uuid4().hex[:12]
    with _SWEEP_LOCK:
        for existing in _STR_JOBS.values():
            if existing["status"] in ("queued", "running"):
                existing["cancelled"] = True
        _STR_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "done": 0,
            "total": total,
            "skipped": len(skipped),
            "current": "Waiting for other heavy jobs…",
            "started_at": time.monotonic(),
            "elapsed_s": 0.0,
            "error": None,
            "report": None,
            "cancelled": False,
        }
        while len(_STR_JOBS) > _MAX_JOBS:
            oldest = next(iter(_STR_JOBS))
            if oldest == job_id:
                break
            _STR_JOBS.pop(oldest, None)

    def _worker() -> None:
        with _SWEEP_RUN_LOCK:
            with _SWEEP_LOCK:
                job = _STR_JOBS.get(job_id)
                if not job or job["cancelled"]:
                    if job:
                        job["status"] = "cancelled"
                    return
                job["status"] = "running"
                job["started_at"] = time.monotonic()

            def _progress(done: int, tot: int, label: str) -> None:
                with _SWEEP_LOCK:
                    j = _STR_JOBS.get(job_id)
                    if j:
                        j["done"] = done
                        j["total"] = tot
                        j["current"] = label

            def _is_cancelled() -> bool:
                with _SWEEP_LOCK:
                    j = _STR_JOBS.get(job_id)
                    return bool(not j or j["cancelled"])

            try:
                report = run_strengths(
                    symbol, period, balance, commission, slippage, daily_filter,
                    progress_cb=_progress, cancel_check=_is_cancelled,
                )
                with _SWEEP_LOCK:
                    j = _STR_JOBS.get(job_id)
                    if j:
                        j.update(status="done", done=total, current="Complete", report=report,
                                 elapsed_s=round(time.monotonic() - j["started_at"], 1))
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                with _SWEEP_LOCK:
                    j = _STR_JOBS.get(job_id)
                    if j:
                        j["status"] = "cancelled" if j["cancelled"] else "error"
                        j["error"] = str(exc)
                        j["elapsed_s"] = round(time.monotonic() - j["started_at"], 1)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"job_id": job_id, "total": total, "skipped": len(skipped)}), 202


@app.route("/strengths/<job_id>")
def strengths_status(job_id: str):
    with _SWEEP_LOCK:
        job = _STR_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Strengths job not found. The server may have restarted; run it again."}), 404
        elapsed = job["elapsed_s"]
        if job["status"] in ("queued", "running"):
            elapsed = round(time.monotonic() - job["started_at"], 1)
        payload = {k: job[k] for k in ("job_id", "status", "done", "total", "skipped", "current", "error")}
        payload["elapsed_s"] = elapsed
        payload["report"] = job["report"] if job["status"] == "done" else None
    return jsonify(payload)


def _strengths_report(job_id: str):
    with _SWEEP_LOCK:
        job = _STR_JOBS.get(job_id)
        return job["report"] if job and job["status"] == "done" else None


@app.route("/strengths/<job_id>.csv")
def strengths_csv_download(job_id: str):
    report = _strengths_report(job_id)
    if not report:
        return Response("Report not ready.\n", status=404, mimetype="text/plain")
    name = f"strengths-{report['symbol']}-{report['period']}.csv"
    return Response(strengths_csv(report), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@app.route("/strengths/<job_id>.json")
def strengths_json_download(job_id: str):
    report = _strengths_report(job_id)
    if not report:
        return Response("Report not ready.\n", status=404, mimetype="text/plain")
    name = f"strengths-{report['symbol']}-{report['period']}.json"
    resp = jsonify(report)
    resp.headers["Content-Disposition"] = f"attachment; filename={name}"
    return resp


@app.route("/export.csv")
def export_csv():
    result = LAST.get("result")
    if not result:
        return Response("Run a backtest first.\n", status=404, mimetype="text/plain")
    name = f"{result['strategy_id']}_{result['symbol']}.csv"
    return Response(
        _csv(result),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


@app.route("/sweep/start", methods=["POST"])
def sweep_start():
    payload = request.get_json(silent=True) or request.form or {}
    try:
        balance = float(payload.get("balance", DEFAULT_BALANCE))
        commission = float(payload.get("commission", DEFAULT_COMMISSION_PCT))
        slippage = float(payload.get("slippage", DEFAULT_SLIPPAGE_PCT))
        raw_daily = payload.get("daily_hama", DEFAULT_DAILY_FILTER)
        daily_filter = raw_daily in (True, 1, "1", "true", "True")
        period = _period(payload.get("period"))
        if balance <= 0:
            raise ValueError("Starting balance must be positive")
        if commission < 0 or slippage < 0:
            raise ValueError("Commission and slippage cannot be negative")
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    canonical, _ = combo_plan()
    total = len(canonical)
    job_id = uuid.uuid4().hex[:12]

    with _SWEEP_LOCK:
        for existing in _SWEEP_JOBS.values():
            if existing["status"] in ("queued", "running"):
                existing["cancelled"] = True
        _SWEEP_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "done": 0,
            "total": total,
            "current": "Preparing full analysis…",
            "started_at": time.monotonic(),
            "elapsed_s": 0.0,
            "error": None,
            "report": None,
            "cancelled": False,
        }
        while len(_SWEEP_JOBS) > _MAX_JOBS:
            oldest_id = next(iter(_SWEEP_JOBS))
            if oldest_id != job_id:
                _SWEEP_JOBS.pop(oldest_id, None)
            else:
                break

    def _worker() -> None:
        with _SWEEP_RUN_LOCK:
            with _SWEEP_LOCK:
                job = _SWEEP_JOBS.get(job_id)
                if not job or job["cancelled"]:
                    if job:
                        job["status"] = "cancelled"
                    return
                job["status"] = "running"
                job["started_at"] = time.monotonic()

            def _progress(done: int, tot: int, label: str) -> None:
                with _SWEEP_LOCK:
                    j = _SWEEP_JOBS.get(job_id)
                    if j:
                        j["done"] = done
                        j["total"] = tot
                        j["current"] = label
                        j["elapsed_s"] = round(time.monotonic() - j["started_at"], 1)

            def _is_cancelled() -> bool:
                with _SWEEP_LOCK:
                    j = _SWEEP_JOBS.get(job_id)
                    return bool(not j or j["cancelled"])

            try:
                report = run_sweep(
                    balance=balance,
                    commission=commission,
                    slippage=slippage,
                    daily_filter=daily_filter,
                    period=period,
                    progress_cb=_progress,
                    cancel_check=_is_cancelled,
                )
                with _SWEEP_LOCK:
                    j = _SWEEP_JOBS.get(job_id)
                    if j:
                        j["status"] = "done"
                        j["done"] = total
                        j["total"] = total
                        j["current"] = "Complete"
                        j["elapsed_s"] = round(time.monotonic() - j["started_at"], 1)
                        j["report"] = report
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                with _SWEEP_LOCK:
                    j = _SWEEP_JOBS.get(job_id)
                    if j:
                        j["status"] = "cancelled" if j["cancelled"] else "error"
                        j["error"] = str(exc)
                        j["elapsed_s"] = round(time.monotonic() - j["started_at"], 1)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return jsonify({"job_id": job_id, "total": total}), 202


@app.route("/sweep/<job_id>")
def sweep_status(job_id: str):
    with _SWEEP_LOCK:
        job = _SWEEP_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Sweep job not found"}), 404
        elapsed = job["elapsed_s"]
        if job["status"] in ("queued", "running"):
            elapsed = round(time.monotonic() - job["started_at"], 1)
        payload = {
            "job_id": job["job_id"],
            "status": job["status"],
            "done": job["done"],
            "total": job["total"],
            "current": job["current"],
            "elapsed_s": elapsed,
            "error": job["error"],
            "report": job["report"] if job["status"] == "done" else None,
        }
    return jsonify(payload)


@app.route("/sweep/<job_id>/report.md")
def sweep_report_md(job_id: str):
    with _SWEEP_LOCK:
        job = _SWEEP_JOBS.get(job_id)
        if not job or job["status"] != "done" or not job["report"]:
            return Response("Report not ready.\n", status=404, mimetype="text/plain")
        md = job["report"]["report_md"]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        md,
        mimetype="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=strategy-report-{stamp}.md"},
    )



@app.route("/ranking/start", methods=["POST"])
def ranking_start():
    payload = request.get_json(silent=True) or request.form or {}
    try:
        balance = float(payload.get("balance", DEFAULT_BALANCE))
        commission = float(payload.get("commission", DEFAULT_COMMISSION_PCT))
        slippage = float(payload.get("slippage", DEFAULT_SLIPPAGE_PCT))
        raw_daily = payload.get("daily_hama", DEFAULT_DAILY_FILTER)
        daily_filter = raw_daily in (True, 1, "1", "true", "True")
        if balance <= 0:
            raise ValueError("Starting balance must be positive")
        if commission < 0 or slippage < 0:
            raise ValueError("Commission and slippage cannot be negative")
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    canonical, _ = ranking_plan()
    total = len(canonical)
    job_id = uuid.uuid4().hex[:12]

    with _SWEEP_LOCK:
        for existing in _RANK_JOBS.values():
            if existing["status"] in ("queued", "running"):
                existing["cancelled"] = True
        _RANK_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "done": 0,
            "total": total,
            "current": "Preparing ranking…",
            "started_at": time.monotonic(),
            "elapsed_s": 0.0,
            "error": None,
            "report": None,
            "cancelled": False,
        }
        while len(_RANK_JOBS) > _MAX_JOBS:
            oldest_id = next(iter(_RANK_JOBS))
            if oldest_id != job_id:
                _RANK_JOBS.pop(oldest_id, None)
            else:
                break

    def _worker() -> None:
        with _SWEEP_RUN_LOCK:
            with _SWEEP_LOCK:
                job = _RANK_JOBS.get(job_id)
                if not job or job["cancelled"]:
                    if job:
                        job["status"] = "cancelled"
                    return
                job["status"] = "running"
                job["started_at"] = time.monotonic()
                job["current"] = "Starting ranking…"

            def _progress(done: int, tot: int, label: str) -> None:
                with _SWEEP_LOCK:
                    j = _RANK_JOBS.get(job_id)
                    if j:
                        j["done"] = done
                        j["total"] = tot
                        j["current"] = label
                        j["elapsed_s"] = round(time.monotonic() - j["started_at"], 1)

            def _is_cancelled() -> bool:
                with _SWEEP_LOCK:
                    j = _RANK_JOBS.get(job_id)
                    return bool(not j or j["cancelled"])

            try:
                report = run_ranking(
                    balance=balance,
                    commission=commission,
                    slippage=slippage,
                    daily_filter=daily_filter,
                    progress_cb=_progress,
                    cancel_check=_is_cancelled,
                )
                with _SWEEP_LOCK:
                    j = _RANK_JOBS.get(job_id)
                    if j:
                        j["status"] = "done"
                        j["done"] = total
                        j["total"] = total
                        j["current"] = "Complete"
                        j["elapsed_s"] = round(time.monotonic() - j["started_at"], 1)
                        j["report"] = report
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                with _SWEEP_LOCK:
                    j = _RANK_JOBS.get(job_id)
                    if j:
                        j["status"] = "cancelled" if j["cancelled"] else "error"
                        j["error"] = str(exc)
                        j["elapsed_s"] = round(time.monotonic() - j["started_at"], 1)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return jsonify({"job_id": job_id, "total": total}), 202


@app.route("/ranking/<job_id>")
def ranking_status(job_id: str):
    with _SWEEP_LOCK:
        job = _RANK_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Ranking job not found"}), 404
        elapsed = job["elapsed_s"]
        if job["status"] in ("queued", "running"):
            elapsed = round(time.monotonic() - job["started_at"], 1)
        payload = {
            "job_id": job["job_id"],
            "status": job["status"],
            "done": job["done"],
            "total": job["total"],
            "current": job["current"],
            "elapsed_s": elapsed,
            "error": job["error"],
            "report": job["report"] if job["status"] == "done" else None,
        }
    return jsonify(payload)


@app.route("/ranking/latest")
def ranking_latest():
    """Latest finished ranking, or a running job's progress, else the bundled default."""
    with _SWEEP_LOCK:
        jobs = list(_RANK_JOBS.values())
    running = [job for job in jobs if job["status"] in ("queued", "running")]
    if running:
        job = max(running, key=lambda item: item["started_at"])
        elapsed = job["elapsed_s"]
        if job["status"] in ("queued", "running"):
            elapsed = round(time.monotonic() - job["started_at"], 1)
        return jsonify({
            "source": "job",
            "job_id": job["job_id"],
            "status": job["status"],
            "done": job["done"],
            "total": job["total"],
            "current": job["current"],
            "elapsed_s": elapsed,
            "error": job["error"],
            "report": None,
        })
    done = [job for job in jobs if job["status"] == "done" and job.get("report")]
    if done:
        job = max(done, key=lambda item: item["started_at"])
        report = annotate_suspect_rows(job["report"])
        report.setdefault("source", "job")
        report.setdefault("source_label", "Latest finished Rank job on this server")
        return jsonify({
            "source": "job",
            "job_id": job["job_id"],
            "status": "done",
            "done": job["done"],
            "total": job["total"],
            "current": job["current"],
            "elapsed_s": job["elapsed_s"],
            "error": None,
            "report": report,
        })
    report = default_ranking_report()
    return jsonify({
        "source": "precomputed",
        "job_id": None,
        "status": "done",
        "done": report.get("summary", {}).get("total_combos"),
        "total": report.get("summary", {}).get("total_combos"),
        "current": "Precomputed default",
        "elapsed_s": report.get("summary", {}).get("elapsed_s"),
        "error": None,
        "report": report,
    })



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False, threaded=True)
