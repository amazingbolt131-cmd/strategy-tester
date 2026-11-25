"""Position book, fills, and per-trade accounting.

Futures PnL is linear in the base quantity: (exit - entry) × qty × side.
Commission is charged on the notional of every fill. Slippage moves the
fill against the order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def round_step(qty: float, step: float) -> float:
    if step <= 0 or qty <= 0:
        return 0.0
    units = math.floor(qty / step + 1e-9)
    decimals = 0
    probe = step
    while probe < 1 and decimals < 8:
        probe *= 10
        decimals += 1
    return round(units * step, decimals)


@dataclass
class Lot:
    side: int
    qty: float
    entry_time: int
    entry_price: float
    anchor: float
    entry_reason: str
    entry_fee: float
    best: float
    worst: float


@dataclass
class Trade:
    side: int
    qty: float
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    entry_reason: str
    exit_reason: str
    pnl: float
    pnl_pct: float
    commission: float
    run_up: float
    drawdown: float
    cumulative: float = 0.0


@dataclass
class Book:
    cash: float
    commission_rate: float
    slippage_rate: float
    step: float
    lots: list[Lot] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    equity_time: list[int] = field(default_factory=list)
    equity_value: list[float] = field(default_factory=list)
    commission_paid: float = 0.0
    peak: float = 0.0
    max_dd: float = 0.0
    max_dd_pct: float = 0.0

    def __post_init__(self) -> None:
        self.peak = self.cash
        self._start = self.cash

    @property
    def side(self) -> int:
        return self.lots[0].side if self.lots else 0

    @property
    def qty(self) -> float:
        return sum(lot.qty for lot in self.lots)

    def avg_entry(self) -> float:
        total = self.qty
        if total <= 0:
            return 0.0
        return sum(lot.entry_price * lot.qty for lot in self.lots) / total

    def _fill(self, raw: float, order_side: int) -> float:
        return raw * (1.0 + self.slippage_rate * order_side)

    def open_lot(self, time: int, raw_price: float, notional: float, side: int, reason: str, high: float, low: float) -> float:
        fill = self._fill(raw_price, side)
        if fill <= 0:
            return 0.0
        return self._open_qty(time, raw_price, fill, round_step(notional / fill, self.step), side, reason, high, low)

    def open_risk(self, time: int, raw_price: float, stop: float, risk: float, side: int, reason: str, high: float, low: float) -> float:
        """Size so a move from the fill to `stop` loses `risk` dollars."""
        fill = self._fill(raw_price, side)
        distance = abs(fill - stop)
        if fill <= 0 or distance <= 0 or risk <= 0:
            return 0.0
        return self._open_qty(time, raw_price, fill, round_step(risk / distance, self.step), side, reason, high, low)

    def _open_qty(self, time: int, raw_price: float, fill: float, qty: float, side: int, reason: str, high: float, low: float) -> float:
        if qty <= 0:
            return 0.0
        fee = fill * qty * self.commission_rate
        self.cash -= fee
        self.commission_paid += fee
        if side == 1:
            best, worst = max(fill, high), min(fill, low)
        else:
            best, worst = min(fill, low), max(fill, high)
        self.lots.append(
            Lot(
                side=side,
                qty=qty,
                entry_time=time,
                entry_price=fill,
                anchor=raw_price,
                entry_reason=reason,
                entry_fee=fee,
                best=best,
                worst=worst,
            )
        )
        return qty

    def mark(self, high: float, low: float) -> None:
        for lot in self.lots:
            if lot.side == 1:
                lot.best = max(lot.best, high)
                lot.worst = min(lot.worst, low)
            else:
                lot.best = min(lot.best, low)
                lot.worst = max(lot.worst, high)

    def _realize(self, lot: Lot, qty: float, fee_share: float, time: int, raw_price: float, reason: str) -> float:
        order_side = -lot.side
        fill = self._fill(raw_price, order_side)
        exit_fee = abs(fill * qty) * self.commission_rate
        gross = (fill - lot.entry_price) * qty * lot.side
        pnl = gross - fee_share - exit_fee
        self.cash += gross - exit_fee
        self.commission_paid += exit_fee
        notional = lot.entry_price * qty
        if lot.side == 1:
            run_up = max(0.0, (lot.best - lot.entry_price) * qty)
            drawdown = max(0.0, (lot.entry_price - lot.worst) * qty)
        else:
            run_up = max(0.0, (lot.entry_price - lot.best) * qty)
            drawdown = max(0.0, (lot.worst - lot.entry_price) * qty)
        self.trades.append(
            Trade(
                side=lot.side,
                qty=qty,
                entry_time=lot.entry_time,
                entry_price=lot.entry_price,
                exit_time=time,
                exit_price=fill,
                entry_reason=lot.entry_reason,
                exit_reason=reason,
                pnl=pnl,
                pnl_pct=(pnl / notional * 100.0) if notional else 0.0,
                commission=fee_share + exit_fee,
                run_up=run_up,
                drawdown=drawdown,
            )
        )
        return pnl

    def _close_qty_from_lot(self, index: int, qty: float, time: int, raw_price: float, reason: str) -> float:
        lot = self.lots[index]
        qty = min(qty, lot.qty)
        fee_share = lot.entry_fee * (qty / lot.qty) if lot.qty else 0.0
        pnl = self._realize(lot, qty, fee_share, time, raw_price, reason)
        lot.qty -= qty
        lot.entry_fee -= fee_share
        if lot.qty <= self.step * 0.5:
            self.lots.pop(index)
        return pnl

    def close_all(self, time: int, raw_price: float, reason: str) -> float:
        pnl = 0.0
        while self.lots:
            lot = self.lots[0]
            pnl += self._close_qty_from_lot(0, lot.qty, time, raw_price, reason)
        return pnl

    def close_last(self, time: int, raw_price: float, reason: str) -> float:
        if not self.lots:
            return 0.0
        lot = self.lots[-1]
        return self._close_qty_from_lot(len(self.lots) - 1, lot.qty, time, raw_price, reason)

    def close_fraction(self, time: int, raw_price: float, fraction: float, reason: str) -> float:
        target = self.qty * fraction
        pnl = 0.0
        while target > self.step * 0.5 and self.lots:
            lot = self.lots[0]
            take = min(lot.qty, target)
            pnl += self._close_qty_from_lot(0, take, time, raw_price, reason)
            target -= take
        return pnl

    def unrealized(self, price: float) -> float:
        return sum((price - lot.entry_price) * lot.qty * lot.side for lot in self.lots)

    def equity(self, price: float) -> float:
        return self.cash + self.unrealized(price)

    def adverse_equity(self) -> float:
        unreal = 0.0
        for lot in self.lots:
            if lot.side == 1:
                unreal += (lot.worst - lot.entry_price) * lot.qty
            else:
                unreal += (lot.entry_price - lot.worst) * lot.qty
        return self.cash + unreal

    def snapshot(self, time: int, close_price: float) -> None:
        eq = self.equity(close_price)
        worst = min(eq, self.adverse_equity()) if self.lots else eq
        stamp = int(time)
        if self.equity_time and self.equity_time[-1] == stamp:
            self.equity_time.pop()
            self.equity_value.pop()
        self.equity_time.append(stamp)
        self.equity_value.append(eq)
        dd = self.peak - worst
        if dd > self.max_dd:
            self.max_dd = dd
            self.max_dd_pct = (dd / self.peak * 100.0) if self.peak else 0.0
        if eq > self.peak:
            self.peak = eq

    def finish(self) -> None:
        running = 0.0
        for trade in self.trades:
            running += trade.pnl
            trade.cumulative = running
