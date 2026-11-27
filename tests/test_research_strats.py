"""Precomputed research strategies: 1y/3y windows and Ranking merge."""

from backtest.config import PERIODS, TESTER_STRATEGIES, symbols_for, venue_enabled
from backtest.data import period_window
from backtest import research_strats
from backtest.ranking import default_ranking_report, ranking_plan
from backtest.runner import run_backtest
from app import app


def test_three_years_follows_one_year_in_the_dropdown():
    keys = list(PERIODS)
    assert keys.index("1y") + 1 == keys.index("3y")
    assert PERIODS["3y"]["label"] == "3 years"
    assert PERIODS["3y"]["start"] == "2023-12-01"
    _, start, end = period_window("3y")
    assert start == 1_701_388_800_000  # 2023-12-01 00:00 UTC


def test_research_strategies_are_in_the_run_dropdown_on_five_coins():
    ids = [s["id"] for s in TESTER_STRATEGIES]
    for sid in research_strats.STRATEGY_IDS:
        assert sid in ids
        assert venue_enabled(next(s for s in TESTER_STRATEGIES if s["id"] == sid), "binance")
        assert not venue_enabled(next(s for s in TESTER_STRATEGIES if s["id"] == sid), "pivex")
        coins = symbols_for(sid, "binance")
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"):
            assert sym in coins


def test_ranking_plan_counts_are_unchanged():
    canonical, _ = ranking_plan()
    assert len(canonical) == 168 + 304


def test_three_year_totals_match_the_research_report():
    data = research_strats.load()
    san = data["sanity"]
    assert san["ladder_ok"] and san["aw_ok"]
    lad = data["windows"]["3y"]["strategies"]["hama_ladder_1m"]["combined"]["stats"]
    aw = data["windows"]["3y"]["strategies"]["aw_scalper_1h"]["combined"]["stats"]
    assert lad["n"] == 5282
    assert abs(lad["tot"] - (-224.2)) < 0.15
    assert aw["n"] == 736
    assert abs(aw["tot"] - 83.7) < 0.15


def test_run_backtest_returns_precomputed_1y_and_3y():
    r = run_backtest("hama_ladder_1m", "BTCUSDT", 100.0, 0.04, 0.0, venue="binance", period="3y", include_chart=False)
    assert r["precomputed"] is True
    assert r["stats"]["total_trades"] > 0
    r2 = run_backtest("aw_scalper_1h", "ETHUSDT", 100.0, 0.04, 0.0, venue="binance", period="1y", include_chart=True)
    assert r2["months"]
    assert r2["stats"]["net_profit_pct"] == r2["stats"]["net_profit"]


def test_ranking_latest_includes_research_and_3y():
    client = app.test_client()
    one = client.get("/ranking/latest?period=1y")
    assert one.status_code == 200
    data = one.get_json()
    assert data["status"] == "done"
    ids = {row["strategy_id"] for row in data["report"]["ranked"]}
    assert "hama_ladder_1m" in ids
    assert "aw_scalper_1h" in ids
    assert "dg_grid_flow" in ids
    three = client.get("/ranking/latest?period=3y")
    assert three.status_code == 200
    d3 = three.get_json()
    ranked = d3["report"]["ranked"]
    assert len(ranked) == 3
    by_id = {r["strategy_id"]: r for r in ranked}
    assert by_id["hama_ladder_1m"]["stats"]["total_trades"] == 5282
    assert abs(by_id["hama_ladder_1m"]["stats"]["net_profit"] - (-224.2)) < 0.15
    assert by_id["aw_scalper_1h"]["stats"]["total_trades"] == 736
    assert abs(by_id["aw_scalper_1h"]["stats"]["net_profit"] - 83.7) < 0.15
    assert "months" in by_id["hama_ladder_1m"]
