"""Server-rendered futures strategy tester."""

from __future__ import annotations

import csv
import io
import traceback

from flask import Flask, Response, render_template, request

from backtest.config import (
    DEFAULT_BALANCE,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_DAILY_FILTER,
    DEFAULT_K,
    DEFAULT_SLIPPAGE_PCT,
    K_MAX,
    K_MIN,
    STRATEGY_BY_ID,
    STRATEGIES,
    SYMBOLS,
    VENUE_LABELS,
    VENUE_ORDER,
    catalog,
    symbols_for,
    venue_enabled,
)
from backtest.runner import run_backtest

app = Flask(__name__)
LAST: dict = {}


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


def _form() -> dict:
    strategy = request.form.get("strategy", STRATEGIES[0]["id"])
    if strategy not in STRATEGY_BY_ID:
        strategy = STRATEGIES[0]["id"]
    venue = request.form.get("venue", "pivex")
    if venue not in VENUE_LABELS or not venue_enabled(STRATEGY_BY_ID[strategy], venue):
        venue = "pivex" if venue_enabled(STRATEGY_BY_ID[strategy], "pivex") else "binance"
    allowed = symbols_for(strategy, venue)
    symbol = request.form.get("symbol", allowed[0])
    if symbol not in allowed:
        symbol = allowed[0]
    if request.method == "POST":
        daily = request.form.get("daily_hama") == "1"
        k_raw = request.form.get("k", str(DEFAULT_K))
    else:
        daily = DEFAULT_DAILY_FILTER
        k_raw = str(int(DEFAULT_K)) if DEFAULT_K == int(DEFAULT_K) else str(DEFAULT_K)
    return {
        "strategy": strategy,
        "venue": venue,
        "symbol": symbol,
        "balance": request.form.get("balance", str(int(DEFAULT_BALANCE))),
        "commission": request.form.get("commission", str(DEFAULT_COMMISSION_PCT)),
        "slippage": request.form.get("slippage", str(DEFAULT_SLIPPAGE_PCT)),
        "k": k_raw,
        "daily_hama": daily,
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
            k = float(form["k"])
            if k < K_MIN or k > K_MAX:
                raise ValueError(f"Size k must be between {K_MIN} and {K_MAX}")
            result = run_backtest(
                form["strategy"],
                form["symbol"],
                balance,
                commission,
                slippage,
                venue=form["venue"],
                k=k,
                daily_filter=bool(form["daily_hama"]),
            )
            metrics = _metrics(result["stats"])
            LAST["result"] = result
        except Exception as exc:  # noqa: BLE001 - show the failure on the page
            error = str(exc)
            traceback.print_exc()
    return render_template(
        "index.html",
        strategies=STRATEGIES,
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False, threaded=True)
