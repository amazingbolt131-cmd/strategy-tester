"""Trade management: partial take-profit keeps a remainder; unload closes only the last level."""

import numpy as np

from backtest.data import Bars
from backtest.engine import Book
from backtest.strategies import run_grid


def _book():
    return Book(cash=10_000.0, commission_rate=0.0, slippage_rate=0.0, step=0.1)


def test_partial_close_leaves_the_rest_open():
    book = _book()
    book.open_lot(0, 100.0, 200.0, 1, "entry", 100.0, 100.0)
    assert book.qty == 2.0
    book.close_fraction(1, 110.0, 0.5, "partial TP")
    assert len(book.trades) == 1
    assert book.trades[0].exit_reason == "partial TP"
    assert book.trades[0].qty == 1.0
    assert abs(book.qty - 1.0) < 1e-9
    assert book.lots[0].entry_reason == "entry"
    book.close_all(2, 112.0, "partial TP")
    assert book.lots == []
    assert book.trades[1].qty == 1.0


def test_unload_closes_only_the_last_grid_level():
    n = 4
    # 1% grid. Enter at 100, add when price trades 99, unload that add at 99.99.
    opens = np.array([100.0, 100.0, 99.0, 99.5])
    highs = np.array([100.5, 100.4, 100.5, 99.6])
    lows = np.array([99.5, 98.5, 98.8, 99.2])
    closes = np.array([100.0, 99.0, 100.0, 99.4])
    bars = Bars(
        open_time=np.arange(n, dtype=np.int64) * 60_000,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        volume=np.ones(n),
    )
    cfg = {
        "direction": "given",
        "direction_values": np.ones(n, dtype=int),
        "signal_tf": "1m",
        "exec_tf": "1m",
        "count": 1,
        "step_pct": 1.0,
        "max_levels": 5,
        "stop_pct": 50.0,
        "level_notional": 100.0,
        "loss_mult": None,
        "max_mult": 1.0,
        "flatten_on_neutral": False,
    }
    book = Book(cash=10_000.0, commission_rate=0.0, slippage_rate=0.0, step=1.0)
    run_grid(bars, bars, book, cfg, test_start=0, test_end=n * 60_000)
    unloads = [t for t in book.trades if t.exit_reason == "unload"]
    assert len(unloads) == 1
    assert unloads[0].entry_reason == "add"
    assert unloads[0].qty == 1.0
    # The original level is still open when the add is unloaded, then the
    # end-of-test close flattens it. It is never closed by the unload.
    assert any(t.entry_reason == "entry" and t.exit_reason == "end of test" for t in book.trades)
    assert not any(t.entry_reason == "entry" and t.exit_reason == "unload" for t in book.trades)


def test_grid_stop_closes_the_basket_and_loss_multiplier_grows_the_next_one():
    n = 3
    bars = Bars(
        open_time=np.arange(n, dtype=np.int64) * 60_000,
        open=np.array([100.0, 100.0, 100.0]),
        high=np.array([100.5, 100.5, 100.5]),
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
        "level_notional": 100.0,
        "loss_mult": 1.5,
        "max_mult": 3.0,
        "flatten_on_neutral": False,
    }
    book = Book(cash=10_000.0, commission_rate=0.0, slippage_rate=0.0, step=0.1)
    run_grid(bars, bars, book, cfg, test_start=0, test_end=n * 60_000)
    assert book.trades[0].exit_reason == "SL"
    assert book.trades[0].qty == 1.0
    # Next basket is 1.5 × $100 at a $100 price → 1.5 contracts.
    assert book.trades[1].entry_reason == "entry"
    assert abs(book.trades[1].qty - 1.5) < 1e-9


def test_commission_is_charged_on_both_fills():
    book = Book(cash=10_000.0, commission_rate=0.001, slippage_rate=0.0, step=0.001)
    book.open_lot(0, 100.0, 100.0, 1, "entry", 101.0, 99.0)
    book.close_all(1, 100.0, "flip")
    # Qty is 1. Two notionals of 100 × 0.1% = 0.20.
    assert abs(book.commission_paid - 0.20) < 1e-9
    assert abs(book.trades[0].pnl - (-0.20)) < 1e-9
