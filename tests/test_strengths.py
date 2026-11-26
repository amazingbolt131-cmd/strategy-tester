"""Strengths run: plan across both venues, metrics, scoring and CSV."""

import json
import math

from backtest import strengths as S
from backtest.config import TESTER_STRATEGIES, venue_enabled

DAY = 86_400_000


def test_plan_covers_every_strategy_on_both_venues():
    rows, skipped = S.strengths_plan("BTCUSDT")
    expected = {
        f"{v}:{s['id']}" for s in TESTER_STRATEGIES for v in ("pivex", "binance") if venue_enabled(s, v)
    }
    assert {r["key"] for r in rows} == expected
    assert not skipped
    assert {r["venue"] for r in rows} == {"pivex", "binance"}
    assert all(r["timeframe"] for r in rows)


def test_plan_skips_strategies_without_the_coin_with_a_note():
    rows, skipped = S.strengths_plan("XRPUSDT")
    assert rows and skipped
    assert all("not offered on XRP" in r["note"] for r in skipped)
    assert {r["key"] for r in rows}.isdisjoint({r["key"] for r in skipped})


def _stats(net, n, wins, dd_pct=2.0, dd=200.0):
    return {
        "net_profit": net, "net_profit_pct": net / 100, "profit_factor": 2.0 if net > 0 else 0.5,
        "win_rate": wins / n * 100 if n else 0, "max_drawdown_pct": dd_pct, "max_drawdown": dd,
        "return_dd_ratio": net / dd, "win_loss_ratio": 1.5, "expectancy": net / n if n else 0,
        "total_trades": n, "wins": wins, "losses": n - wins, "max_consec_losses": 2, "max_consec_wins": 3,
        "avg_duration_hours": 5.0, "long_trades": n, "long_net_profit": net, "long_win_rate": 50.0,
        "short_trades": 0, "short_net_profit": 0.0, "short_win_rate": 0.0, "largest_win": 100.0,
        "largest_loss": -50.0, "time_in_market_pct": 10.0, "commission_paid": 20.0,
    }


def _trades(pnls, start):
    return [{"side": 1, "entry_ms": start + i * DAY, "exit_ms": start + i * DAY + 3_600_000, "pnl": p, "commission": 1.0}
            for i, p in enumerate(pnls)]


def test_metrics_scoring_and_csv():
    start, end = 0, 30 * DAY
    rows = []
    for i, (venue, net) in enumerate([("pivex", 900.0), ("binance", 300.0), ("binance", -200.0), ("pivex", 50.0)]):
        pnls = [net / 5] * 5
        res = {"venue": venue, "stats": _stats(net, 5, 5 if net > 0 else 0, dd=700.0 if venue == "pivex" and i == 0 else 100.0),
               "trades": _trades(pnls, start)}
        m = S.row_metrics(res, 10_000.0, start, end)
        rows.append({"key": f"{venue}:s{i}", "venue": venue, "venue_label": venue.title(), "strategy": f"S{i}",
                     "timeframe": "5m", "symbol": "BTCUSDT", "metrics": m})
    assert rows[0]["metrics"]["bucket"] == "weeks"
    assert rows[0]["metrics"]["pivex_dd_breach"] is True
    assert "pivex_info" not in rows[1]["metrics"]
    leaders = S.score_rows(rows)
    most = next(c for c in leaders if c["key"] == "net_profit")
    assert [t["key"] for t in most["top"]] == ["pivex:s0", "binance:s1", "pivex:s3"]
    assert rows[0]["score"] >= 3
    assert any("Loses money" in w for w in rows[2]["weak"])
    assert any("$600" in w for w in rows[0]["weak"])
    # Lowest drawdown needs a profit, so the losing row is not eligible.
    low_dd = next(c for c in leaders if c["key"] == "max_dd_pct")
    assert "binance:s2" not in [t["key"] for t in low_dd["top"]]
    report = {"rows": S._clean(rows), "skipped": [], "symbol": "BTCUSDT", "period": "1m"}
    json.dumps(report, allow_nan=False)
    csv_text = S.to_csv(report)
    assert csv_text.splitlines()[0].startswith("Venue,Strategy,Timeframe")
    assert len(csv_text.strip().splitlines()) == 5


def test_month_buckets_for_long_windows():
    start = 1_735_689_600_000  # 2025-01-01 UTC
    bucket, pnls = S._bucket_pnls(_trades([10, -5, 3], start), start, start + 90 * DAY)
    assert bucket == "months" and len(pnls) == 3
    assert math.isclose(sum(pnls), 8.0)
