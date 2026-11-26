"""Multi-coin Strengths: plan with every coin, cross-coin aggregate, isolation, endpoint."""

import io
import json
import os

import pytest

from backtest import strengths as S
from backtest import v31
from backtest.data import Bars

import numpy as np


def test_all_coins_plan_runs_every_strategy_and_marks_off_list_rows():
    rows, skipped = S.strengths_plan("XRPUSDT", all_coins=True)
    full, _ = S.strengths_plan("BTCUSDT")
    assert not skipped
    assert {r["key"] for r in rows} == {r["key"] for r in full}
    off = [r for r in rows if r.get("off_list")]
    assert off and all("not one of" in r["list_note"] for r in off)
    live, _ = S.strengths_plan("XRPUSDT")
    assert {r["key"] for r in rows if not r.get("off_list")} == {r["key"] for r in live}


def _m(net, pf=2.0, wr=50.0, dd=5.0, trades=10, green=50.0, pf_inf=False):
    return {
        "net_profit": net, "return_pct": net / 100, "profit_factor": None if pf_inf else pf, "pf_infinite": pf_inf,
        "win_rate": wr, "max_dd_pct": dd, "max_dd": dd * 100, "recovery_factor": 1.0, "rr": 1.2, "expectancy": net / trades,
        "trades": trades, "wins": 5, "losses": 5, "max_loss_streak": 3, "max_win_streak": 4, "bucket": "months", "buckets": 12,
        "green_pct": green, "period_std": 10.0, "sharpe": 1.0, "sortino": 1.5, "avg_hold_h": 3.0, "long_trades": trades,
        "long_net": net, "long_win_rate": wr, "short_trades": 0, "short_net": 0.0, "short_win_rate": 0.0,
        "best_trade": 100.0, "worst_trade": -50.0, "time_in_market_pct": 10.0, "fees": 10.0, "fee_drag_pct": 5.0,
        "worst_day": -60.0, "peak_net": max(net, 0),
    }


def _rep(sym, rows):
    out = []
    for key, m in rows:
        venue, sid = key.split(":")
        out.append({"key": key, "venue": venue, "venue_label": venue.title(), "strategy_id": sid, "strategy": sid,
                    "timeframe": "5m", "symbol": sym, "symbol_label": sym[:3], "metrics": m,
                    "error": None if m else "boom", "badges": [{"key": "win_rate", "rank": 1, "text": "High win rate"}] if m and m["win_rate"] > 60 else []})
    return {"symbol": sym, "rows": out}


def test_aggregate_counts_medians_and_reliability():
    reps = [
        _rep("BTCUSDT", [("pivex:a", _m(500, pf=3.0, wr=70, dd=4)), ("binance:b", _m(-100, pf=0.8, dd=12))]),
        _rep("ETHUSDT", [("pivex:a", _m(300, pf=2.0, wr=65, dd=6)), ("binance:b", _m(50, pf=1.1, dd=8))]),
        _rep("SOLUSDT", [("pivex:a", _m(-50, pf=0.9, wr=40, dd=9)), ("binance:b", None)]),
        _rep("XRPUSDT", [("pivex:a", _m(200, pf_inf=True, wr=80, dd=2)), ("binance:b", _m(-20, pf=0.9, dd=3))]),
        _rep("BNBUSDT", [("pivex:a", _m(100, pf=1.5, wr=55, dd=3)), ("binance:b", _m(-10, pf=0.95, dd=2))]),
    ]
    agg = S.aggregate_reports(reps)
    a = next(r for r in agg["rows"] if r["key"] == "pivex:a")
    b = next(r for r in agg["rows"] if r["key"] == "binance:b")
    am = a["metrics"]
    assert am["coins_run"] == 5 and am["profitable"] == 4
    assert am["total_net"] == pytest.approx(1050)
    assert am["median_pf"] == pytest.approx(2.0)           # 0.9, 1.5, 2.0, 3.0, 99
    assert am["worst_dd_pct"] == pytest.approx(9) and am["worst_dd_coin"] == "SOL"
    assert am["median_win_rate"] == pytest.approx(65)
    assert any("top 3 on 3 of 5" in x for x in a["reliable"])   # win-rate badge on BTC, ETH, XRP
    assert b["metrics"]["coins_run"] == 4 and b["metrics"]["coins_failed"] == 1
    assert any("Failed on SOL" in w for w in b["weak"])
    assert any("only 1 of 4" in w for w in b["weak"])
    assert a["score"] > b["score"]
    rep = {"symbol_label": "BTC, ETH", "period_label": "1 year", "window_label": "x", "aggregate": agg,
           "reports": [dict(r, skipped=[]) for r in reps]}
    for r in rep["reports"]:
        for row in r["rows"]:
            row.setdefault("weak", [])
    csv_text = S.to_csv_multi(rep)
    assert "Cross-coin table" in csv_text and "Per-coin rows" in csv_text


def test_multi_needs_five_coins():
    with pytest.raises(ValueError, match="at least 5"):
        S.run_strengths_multi(["BTCUSDT", "ETHUSDT"], "1m", 10000, 0.04, 0, True)


def test_streamed_payload_matches_json_dumps():
    t = np.arange(5, dtype=np.int64) * 60_000
    bars = Bars(t, np.array([1.1, 2.0, 3.25, 1e-7, 12345.678]), np.ones(5) * 2.5, np.ones(5) * 0.1, np.ones(5) / 3, np.ones(5))
    rows = v31._rows(bars)
    payload = {"id": "x", "flag": True, "series": {"m1": rows, "h1": rows, "sol": [[1, 2.5]]}, "k": 8.0}
    buf = io.StringIO()
    v31._write_json(buf, payload)
    plain = dict(payload, series={"m1": rows.to_list(), "h1": rows.to_list(), "sol": [[1, 2.5]]})
    assert buf.getvalue() == json.dumps(plain)


@pytest.mark.skipif(not hasattr(os, "fork") or not os.path.isdir("/proc"), reason="needs fork and /proc")
def test_isolated_row_reports_memory_stop_and_crash(monkeypatch):
    def hog(*a, **k):
        x = bytearray(300 * 1024 * 1024)
        for i in range(0, len(x), 4096):
            x[i] = 1
        import time
        time.sleep(5)
        return {"metrics": {}, "note": ""}

    monkeypatch.setattr(S, "_compute_row", hog)
    out = S._run_row_isolated({"strategy_id": "x", "venue": "pivex"}, "BTCUSDT", (0, 0, 0), 1, 0, 0, True, budget_mb=150)
    assert "out of memory" in out["error"] and out["peak_mb"] > 150

    def die(*a, **k):
        os.kill(os.getpid(), 9)

    monkeypatch.setattr(S, "_compute_row", die)
    out = S._run_row_isolated({"strategy_id": "x", "venue": "pivex"}, "BTCUSDT", (0, 0, 0), 1, 0, 0, True, budget_mb=None)
    assert "killed" in out["error"]

    monkeypatch.setattr(S, "_compute_row", lambda *a, **k: {"metrics": {"net_profit": 1.5}, "note": "n"})
    out = S._run_row_isolated({"strategy_id": "x", "venue": "pivex"}, "BTCUSDT", (0, 0, 0), 1, 0, 0, True, budget_mb=None)
    assert out["metrics"]["net_profit"] == 1.5 and out["note"] == "n"


def test_start_endpoint_validates_coin_count():
    import app as app_module

    client = app_module.app.test_client()
    resp = client.post("/strengths/start", json={"symbols": ["BTCUSDT", "ETHUSDT"], "period": "1y"})
    assert resp.status_code == 400
    assert "at least 5" in resp.get_json()["error"]
    resp = client.post("/strengths/start", json={"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "NOPEUSDT"]})
    assert resp.status_code == 400


def test_page_has_multi_coin_controls():
    import app as app_module

    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert 'name="strengths-coin"' in html and 'id="strengths-period"' in html
    for sym in S.DEFAULT_SYMBOLS:
        assert f'value="{sym}" checked' in html
    assert '<option value="1y" selected' in html


def test_ml_rows_under_50_cents_are_left_out_of_the_aggregate():
    reps = [_rep(sym, [("binance:ml_5m", _m(n))]) for sym, n in
            [("BTCUSDT", 100), ("ETHUSDT", 200), ("SOLUSDT", 300), ("XRPUSDT", 400), ("DOGEUSDT", -5_000_000)]]
    reps[-1]["rows"][0]["ml_low_price"] = True
    agg = S.aggregate_reports(reps)
    row = agg["rows"][0]
    assert row["metrics"]["coins_run"] == 4 and row["metrics"]["total_net"] == 1000
    assert row["excluded_coins"] == ["DOG"]
    assert any("Left out of the cross-coin numbers" in w for w in row["weak"])
    assert [p["excluded"] for p in row["per_coin"]] == [False] * 4 + [True]
