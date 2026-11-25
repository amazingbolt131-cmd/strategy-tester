"""Dollar risk, the HAMA step ladder, the S5 2x switch, and the DG basket ladder."""

import numpy as np

from backtest.data import Bars
from backtest.engine import Book
import pytest

from backtest.config import resolve_strategy
from backtest.risk import doubling_multiplier, ladder_multiplier, loss_multiplier, stepped_risk
from backtest.strategies import run_grid


def test_hama_step_starts_at_one_and_caps_at_eight():
    assert stepped_risk(7.59, 0, 8) == 7.59
    assert stepped_risk(7.59, 1, 8) == 15.18
    assert abs(stepped_risk(7.59, 7, 8) - 60.72) < 1e-9
    assert stepped_risk(7.59, 40, 8) == stepped_risk(7.59, 7, 8)
    assert stepped_risk(20.74, 5, 1) == 20.74


def test_s5_doubles_after_two_losses_and_caps_at_2x():
    assert loss_multiplier(0, 2, 2.0, 2.0) == 1.0
    assert loss_multiplier(1, 2, 2.0, 2.0) == 1.0
    assert loss_multiplier(2, 2, 2.0, 2.0) == 2.0
    assert loss_multiplier(9, 2, 2.0, 2.0) == 2.0


def test_venue_cards_and_binance_only_strategies():
    pivex = resolve_strategy("s5_15m", "pivex")
    binance = resolve_strategy("s5_15m", "binance")
    assert pivex["risk"] == 111.41632457034615 and pivex["loss_mode"] == "after" and pivex["mult_cap"] == 2.0
    assert binance["risk"] == 22.085622688828757 and binance["loss_mode"] == "double" and binance["mult_cap"] == 5
    dg = resolve_strategy("dg_grid", "binance")
    assert dg["risk"] == 1000.0 and dg["loss_mode"] == "flat"
    with pytest.raises(ValueError):
        resolve_strategy("dg_grid", "pivex")
    with pytest.raises(ValueError):
        resolve_strategy("er_grid", "pivex")
    with pytest.raises(ValueError):
        resolve_strategy("hama_grid_1m", "pivex")
    resolve_strategy("micro_1h", "binance")
    resolve_strategy("turtle_5m", "binance")
    resolve_strategy("hama_30m", "binance")


def test_binance_doubling_caps():
    assert [doubling_multiplier(n, 5) for n in range(5)] == [1, 2, 4, 5, 5]
    assert [doubling_multiplier(n, 10) for n in range(6)] == [1, 2, 4, 8, 10, 10]


def test_dg_adds_one_times_every_three_losing_baskets():
    assert [ladder_multiplier(n, 3, 5) for n in (0, 2, 3, 5, 6, 12, 40)] == [1, 1, 2, 2, 3, 5, 5]


def test_open_risk_is_risk_over_stop_distance():
    book = Book(cash=10_000.0, commission_rate=0.0, slippage_rate=0.0, step=0.001)
    # $20.74 of risk, stop $100 away → 0.207 contracts.
    qty = book.open_risk(0, 1000.0, 900.0, 20.74, 1, "entry", 1000.0, 1000.0)
    assert qty == 0.207


def test_dg_ladder_grows_the_next_basket():
    n = 3
    bars = Bars(
        open_time=np.arange(n, dtype=np.int64) * 60_000,
        open=np.array([100.0, 100.0, 100.0]),
        high=np.array([100.2, 100.2, 100.2]),
        low=np.array([99.5, 40.0, 99.5]),
        close=np.array([100.0, 50.0, 100.0]),
        volume=np.ones(n),
    )
    cfg = {
        "direction": "given",
        "direction_values": np.ones(n, dtype=int),
        "signal_tf": "1m",
        "exec_tf": "1m",
        "count": 1,
        "step_pct": 1.0,
        "max_levels": 4,
        "stop_pct": 10.0,
        "risk": 10.0,
        "loss_every": 1,
        "max_mult": 5,
        "flatten_on_neutral": False,
    }
    book = Book(cash=10_000.0, commission_rate=0.0, slippage_rate=0.0, step=0.1)
    run_grid(bars, bars, book, cfg, test_start=0, test_end=n * 60_000)
    # $10 risk / 10% of 100 = 1 contract. One losing basket steps the ladder to 2x.
    assert book.trades[0].exit_reason == "SL"
    assert abs(book.trades[0].qty - 1.0) < 1e-9
    assert abs(book.trades[1].qty - 2.0) < 1e-9
