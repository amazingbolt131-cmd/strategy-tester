"""Ranking tab: plan, best-symbol pick, metadata, and API — no live engine runs."""

from unittest.mock import patch

import pytest

from backtest.config import CORE_SYMBOLS, symbols_for, venue_enabled, STRATEGIES, SYMBOLS
from backtest.ranking import (
    BINANCE_RANKING_SYMBOLS,
    NATIVE_TF_NOTE,
    PIVEX_RANKING_SYMBOLS,
    best_per_strategy,
    ranking_plan,
    rank_rows,
    run_ranking,
    split_tp_sl,
    strategy_rule_fields,
)
from backtest.sweep import STRATEGY_SPECS

from app import app


def test_binance_universe_is_38_unique_usdt_symbols():
    assert len(BINANCE_RANKING_SYMBOLS) == 38
    assert len(set(BINANCE_RANKING_SYMBOLS)) == 38
    assert all(symbol.endswith("USDT") and symbol in SYMBOLS for symbol in BINANCE_RANKING_SYMBOLS)
    # Highest-liquidity majors must be present.
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "LTCUSDT"):
        assert symbol in BINANCE_RANKING_SYMBOLS


def test_pivex_universe_is_the_known_listed_coins_only():
    assert PIVEX_RANKING_SYMBOLS == [
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "LTCUSDT",
        "DOGEUSDT",
        "SOLUSDT",
    ]
    assert all(symbol in SYMBOLS for symbol in PIVEX_RANKING_SYMBOLS)


def test_ranking_symbols_do_not_change_run_or_start_dropdowns():
    assert CORE_SYMBOLS == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert "LTCUSDT" not in symbols_for("turtle_5m", "pivex")
    assert "HYPEUSDT" not in symbols_for("turtle_5m", "binance")
    assert "ADAUSDT" not in symbols_for("s5_15m", "binance")
    # Existing extras stay as they were.
    assert "XRPUSDT" in symbols_for("micro_1h", "pivex")
    assert "ARBUSDT" in symbols_for("s5_15m", "binance")


def test_ranking_plan_covers_enabled_strategies_only():
    canonical, exec_order = ranking_plan()
    assert len(canonical) == len(exec_order)
    assert {row["order_idx"] for row in canonical} == {row["order_idx"] for row in exec_order}

    pivex = [row for row in canonical if row["venue"] == "pivex"]
    binance = [row for row in canonical if row["venue"] == "binance"]
    pivex_strats = {row["strategy_id"] for row in pivex}
    binance_strats = {row["strategy_id"] for row in binance}

    for strategy in STRATEGIES:
        if venue_enabled(strategy, "pivex"):
            assert strategy["id"] in pivex_strats
            expected = 6
            assert sum(1 for row in pivex if row["strategy_id"] == strategy["id"]) == expected
            if strategy["id"] == "sol1m_morning":
                coins = [row["symbol"] for row in pivex if row["strategy_id"] == strategy["id"]]
                assert coins == PIVEX_RANKING_SYMBOLS
        else:
            assert strategy["id"] not in pivex_strats
        if venue_enabled(strategy, "binance") or strategy["id"] == "sol1m_morning":
            assert strategy["id"] in binance_strats
            assert sum(1 for row in binance if row["strategy_id"] == strategy["id"]) == 38
        else:
            assert strategy["id"] not in binance_strats

    # 7 Pivex strategies x 6 coins + 8 Binance strategies x 38 (Morning HAMA added on Binance for Ranking only)
    assert len(pivex) == 7 * 6
    assert len(binance) == 8 * 38
    assert len(canonical) == 42 + 304


def test_split_tp_sl_uses_existing_sentences_only():
    tp, sl = split_tp_sl(
        "SL = entry -/+ 1.5 x ATR(10) of signal 1h bar; TP = entry +/- 3.0 x ATR(10) (fixed 2.0R target)."
    )
    assert tp == "TP = entry +/- 3.0 x ATR(10) (fixed 2.0R target)."
    assert sl == "SL = entry -/+ 1.5 x ATR(10) of signal 1h bar;"
    empty_tp, empty_sl = split_tp_sl("")
    assert empty_tp == "—" and empty_sl == "—"


def test_rule_fields_come_from_existing_specs():
    for strategy_id in STRATEGY_SPECS:
        fields = strategy_rule_fields(strategy_id)
        spec = STRATEGY_SPECS[strategy_id]
        assert fields["indicators"] == spec["indicators"]
        assert fields["signal_tf"] == spec["signal_tf"]
        assert fields["execution_tf"] == spec["execution_tf"]
        assert fields["tp"] != ""
        assert fields["sl"] != ""
        # Do not invent: every non-dash fragment must appear in the original tp_sl text.
        for part in (fields["tp"], fields["sl"]):
            if part != "—":
                for sentence in part.split():
                    assert sentence in spec["tp_sl"]


def test_best_per_strategy_picks_highest_net_profit():
    rows = [
        _row("binance", "turtle_5m", "ETHUSDT", net=923.16, pf=1.2, trades=16),
        _row("binance", "turtle_5m", "BTCUSDT", net=50.0, pf=2.0, trades=8),
        _row("binance", "ml_5m", "SOLUSDT", net=-10.0, pf=0.8, trades=4),
        _row("binance", "ml_5m", "ETHUSDT", net=25.0, pf=1.1, trades=9),
        _row("pivex", "turtle_5m", "BTCUSDT", net=-5.0, pf=0.9, trades=3),
        {"venue": "pivex", "venue_label": "Pivex", "strategy_id": "turtle_5m",
         "strategy_label": "Reversal Turtle MTF · 5m", "symbol": "ETHUSDT",
         "symbol_label": "ETH", "stats": None, "error": "boom"},
    ]
    winners = best_per_strategy(rows)
    by_key = {(row["venue"], row["strategy_id"]): row for row in winners}
    assert by_key[("binance", "turtle_5m")]["symbol"] == "ETHUSDT"
    assert by_key[("binance", "turtle_5m")]["symbols_tested"] == 2
    assert by_key[("binance", "ml_5m")]["symbol"] == "ETHUSDT"
    assert by_key[("pivex", "turtle_5m")]["symbol"] == "BTCUSDT"
    assert by_key[("pivex", "turtle_5m")]["failed_symbols"] == 1
    # Default display order is PF descending.
    assert winners[0]["strategy_id"] == "turtle_5m" and winners[0]["venue"] == "binance"


def test_rank_rows_puts_infinite_pf_first_and_errors_last():
    rows = [
        _row("binance", "a", "ETHUSDT", net=10, pf=1.5, trades=2),
        _row("binance", "b", "ETHUSDT", net=8, pf=None, trades=2),
        {"venue": "binance", "venue_label": "Binance", "strategy_id": "c",
         "strategy_label": "c", "symbol": "ETHUSDT", "symbol_label": "ETH",
         "stats": None, "error": "fail"},
    ]
    ordered = rank_rows(rows)
    assert [row["strategy_id"] for row in ordered] == ["b", "a", "c"]
    assert [row["rank"] for row in ordered] == [1, 2, 3]


def test_run_ranking_uses_native_timeframes_and_default_window():
    seen = []

    def fake_run(strategy_id, symbol, balance, commission, slippage, **kwargs):
        seen.append((strategy_id, symbol, kwargs))
        return {
            "stats": {
                "net_profit": 1.0,
                "net_profit_pct": 0.01,
                "profit_factor": 1.1,
                "max_drawdown": 1.0,
                "max_drawdown_pct": 0.01,
                "total_trades": 1,
                "win_rate": 100.0,
                "avg_win": 1.0,
                "avg_loss": 0.0,
                "expectancy": 1.0,
                "win_loss_ratio": None,
                "commission_paid": 0.1,
            },
            "window_label": "2026-10-26 → 2026-11-24 UTC",
            "note": "",
        }

    window = (1, 2, 3)
    with patch("backtest.ranking.ranking_window", return_value=window), patch(
        "backtest.ranking.run_backtest", side_effect=fake_run
    ):
        report = run_ranking(10_000, 0.04, 0.0, True)

    assert seen, "expected mocked backtests"
    morning = [item for item in seen if item[0] == "sol1m_morning"]
    others = [item for item in seen if item[0] != "sol1m_morning"]
    assert morning
    morning_syms = sorted({item[1] for item in morning})
    assert "SOLUSDT" in morning_syms
    assert "BTCUSDT" in morning_syms
    assert "ETHUSDT" in morning_syms
    assert len(morning) == 6 + 38  # 6 Pivex + 38 Binance
    for _strategy_id, _symbol, kwargs in seen:
        assert kwargs["window"] == window
        assert "timeframe" not in kwargs
        assert "tf" not in kwargs
        assert kwargs["include_chart"] is False
        assert kwargs["skip_missing_days"] is True
        assert kwargs["k"] == 8.0
    for _strategy_id, _symbol, kwargs in morning:
        assert kwargs.get("profit_target") == 1000.0
    for _strategy_id, _symbol, kwargs in others:
        assert "profit_target" not in kwargs
    assert report["settings"]["native_timeframes"] == NATIVE_TF_NOTE
    assert report["morning_hama"]["profit_target"] == 1000.0
    assert report["summary"]["total_combos"] == 346
    assert report["summary"]["strategies_ranked"] == 15
    assert len(report["ranked"]) == 15
    assert all(row["symbol"] in SYMBOLS for row in report["ranked"])


def test_ranking_start_endpoint_uses_default_balance(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr(
        "app.run_ranking",
        lambda **kwargs: {
            "window_label": "test window",
            "settings": {"balance": kwargs["balance"], "native_timeframes": NATIVE_TF_NOTE},
            "universe": {},
            "summary": {"total_combos": 1, "completed_combos": 1, "failed_combos": 0, "strategies_ranked": 1, "elapsed_s": 0.1},
            "ranked": [],
            "all_results": [],
        },
    )

    # Invalid payload
    bad = client.post("/ranking/start", json={"balance": 0})
    assert bad.status_code == 400

    started = client.post("/ranking/start", json={})
    assert started.status_code == 202
    job_id = started.get_json()["job_id"]
    assert started.get_json()["total"] == 346

    # Poll until the background worker finishes (mocked, should be fast).
    payload = None
    for _ in range(50):
        payload = client.get(f"/ranking/{job_id}").get_json()
        if payload["status"] in {"done", "error", "cancelled"}:
            break
        import time as _time
        _time.sleep(0.02)
    assert payload is not None
    assert payload["status"] == "done"
    assert payload["report"]["settings"]["balance"] == 10_000.0


def _row(venue, strategy_id, symbol, net, pf, trades):
    return {
        "venue": venue,
        "venue_label": "Binance" if venue == "binance" else "Pivex",
        "strategy_id": strategy_id,
        "strategy_label": strategy_id,
        "symbol": symbol,
        "symbol_label": symbol.replace("USDT", ""),
        "stats": {
            "net_profit": net,
            "net_profit_pct": net / 100.0,
            "profit_factor": pf,
            "max_drawdown": 1.0,
            "max_drawdown_pct": 1.0,
            "total_trades": trades,
            "win_rate": 50.0,
            "avg_win": 1.0,
            "avg_loss": -1.0,
            "expectancy": net / trades if trades else 0.0,
            "win_loss_ratio": 1.0,
            "commission_paid": 0.5,
        },
        "error": None,
    }

def test_ranking_window_is_one_year_utc():
    from datetime import datetime, timezone
    from backtest.data import RANKING_WINDOW_LABEL, ranking_window

    warmup, start, end = ranking_window()
    assert datetime.fromtimestamp(start / 1000, tz=timezone.utc) == datetime(2025, 11, 25, tzinfo=timezone.utc)
    assert datetime.fromtimestamp(end / 1000, tz=timezone.utc) == datetime(2026, 11, 25, tzinfo=timezone.utc)
    assert start - warmup == 40 * 24 * 60 * 60 * 1000
    assert "former 2026-07-01 to 2026-10-25 holdout" in RANKING_WINDOW_LABEL


def test_ranking_latest_returns_bundled_default(monkeypatch):
    from backtest import ranking as ranking_mod

    fake = {
        "window_label": ranking_mod.RANKING_WINDOW_LABEL,
        "source": "bundled_default",
        "source_label": "precomputed test",
        "settings": {"balance": 10000, "native_timeframes": NATIVE_TF_NOTE},
        "universe": {},
        "summary": {"total_combos": 303, "completed_combos": 303, "failed_combos": 0, "strategies_ranked": 14, "elapsed_s": 1},
        "ranked": [{"rank": 1, "strategy_id": "s5_15m", "venue": "pivex", "symbol": "LTCUSDT", "stats": {"net_profit": 1}}],
        "all_results": [],
        "suspect_warnings": [],
    }
    monkeypatch.setattr("app.default_ranking_report", lambda: fake)
    # No in-memory jobs
    monkeypatch.setattr("app._RANK_JOBS", {})
    client = app.test_client()
    resp = client.get("/ranking/latest")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "done"
    assert body["source"] == "precomputed"
    assert body["report"]["ranked"]
    assert "2025-11-25" in body["report"]["window_label"]
    assert "holdout" in body["report"]["window_label"]

