"""Dollar-risk position sizing.

Contracts = risk_usd / |entry - stop|. The step and loss ladders scale the
dollar risk before that division. A winning basket resets the ladder.
"""

from __future__ import annotations


def stepped_risk(unit: float, losses: int, max_step: int) -> float:
    """HAMA 1h: step 1 at zero losses, +1 step per loss, capped at max_step.

    HAMA 30m uses max_step 1, so the risk stays at `unit`.
    """
    step = 1 if max_step <= 1 else min(max_step, 1 + max(0, losses))
    return float(unit) * step


def loss_multiplier(losses: int, after: int, multiplier: float, cap: float) -> float:
    """S5: 1x until `after` consecutive losses, then `multiplier`, never above `cap`."""
    if after <= 0 or losses < after:
        return 1.0
    return min(float(cap), float(multiplier))


def doubling_multiplier(losses: int, cap: float) -> float:
    """Binance ladders: 1x, then double after each losing basket, never above `cap`."""
    if losses <= 0:
        return 1.0
    return min(float(cap), float(2 ** int(losses)))


def ladder_multiplier(losing_baskets: int, every: int, cap: int) -> int:
    """DG: +1x for every `every` consecutive losing baskets, starting at 1x."""
    if every <= 0:
        return 1
    return min(int(cap), 1 + max(0, losing_baskets) // int(every))
