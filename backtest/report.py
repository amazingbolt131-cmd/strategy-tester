"""Print a strategy × symbol summary table for the last published month."""

from __future__ import annotations

from .config import (
    CORE_SYMBOLS,
    DEFAULT_BALANCE,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_DAILY_FILTER,
    DEFAULT_K,
    DEFAULT_SLIPPAGE_PCT,
    STRATEGIES,
    SYMBOLS,
    VENUE_ORDER,
    venue_enabled,
)
from .data import test_window
from .runner import run_backtest


def main() -> None:
    window = test_window()
    header = f"{'Venue':<8} {'Strategy':<28} {'Symbol':>6} {'Net %':>10} {'PF':>8} {'Max DD %':>10} {'Trades':>8}"
    print(header)
    print("-" * len(header))
    for venue in VENUE_ORDER:
        for strategy in STRATEGIES:
            if not venue_enabled(strategy, venue):
                continue
            for symbol in CORE_SYMBOLS:
                result = run_backtest(
                    strategy["id"],
                    symbol,
                    DEFAULT_BALANCE,
                    DEFAULT_COMMISSION_PCT,
                    DEFAULT_SLIPPAGE_PCT,
                    window=window,
                    venue=venue,
                    k=DEFAULT_K,
                    daily_filter=DEFAULT_DAILY_FILTER,
                )
                stats = result["stats"]
                pf = stats["profit_factor"]
                pf_txt = "inf" if pf is None else f"{pf:.2f}"
                print(
                    f"{venue:<8} {strategy['label']:<28} {SYMBOLS[symbol]['label']:>6} "
                    f"{stats['net_profit_pct']:>9.2f}% {pf_txt:>8} "
                    f"{stats['max_drawdown_pct']:>9.2f}% {stats['total_trades']:>8}"
                )


if __name__ == "__main__":
    main()
