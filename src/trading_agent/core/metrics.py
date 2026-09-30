"""Performance metrics computed from an equity curve and a list of fills.

Used by: backtests (to gate proposals), the evolution loop (to compare champion vs challenger),
the daily report, and Prometheus gauges. One implementation, one definition of "better".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from trading_agent.core.models import Fill, Side


@dataclass
class RoundTrip:
    symbol: str
    qty: float
    entry_price: float
    exit_price: float
    fees: float

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty - self.fees


@dataclass
class PerformanceReport:
    start_equity: float
    end_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    trades: int
    win_rate: float
    profit_factor: float
    fees_paid: float
    fees_pct_of_start_equity: float
    round_trips: list[RoundTrip] = field(default_factory=list)

    def summary(self) -> dict[str, float | int]:
        return {
            "total_return_pct": round(self.total_return_pct, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 3),
            "sharpe": round(self.sharpe, 3),
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 3),
            "fees_paid": round(self.fees_paid, 4),
            "fees_pct_of_start_equity": round(self.fees_pct_of_start_equity, 3),
        }


def max_drawdown_pct(equity: list[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, (peak - e) / peak * 100.0)
    return worst


def sharpe_ratio(equity: list[float], periods_per_year: float) -> float:
    if len(equity) < 3:
        return 0.0
    rets = [(b - a) / a for a, b in zip(equity[:-1], equity[1:], strict=False) if a > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(periods_per_year)


def round_trips(fills: list[Fill]) -> list[RoundTrip]:
    """FIFO-match buys and sells per (sleeve, symbol) into closed trades."""
    lots: dict[tuple[str, str], list[list[float]]] = {}  # key -> [[qty, price, fee_per_unit], ...]
    trips: list[RoundTrip] = []
    for f in sorted(fills, key=lambda x: x.ts):
        key = (f.sleeve_id, f.symbol)
        book = lots.setdefault(key, [])
        if f.side is Side.BUY:
            book.append([f.qty, f.price, f.fee_quote / f.qty if f.qty else 0.0])
            continue
        remaining = f.qty
        exit_fee_per_unit = f.fee_quote / f.qty if f.qty else 0.0
        while remaining > 1e-12 and book:
            lot = book[0]
            take = min(lot[0], remaining)
            trips.append(
                RoundTrip(
                    symbol=f.symbol,
                    qty=take,
                    entry_price=lot[1],
                    exit_price=f.price,
                    fees=take * (lot[2] + exit_fee_per_unit),
                )
            )
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-12:
                book.pop(0)
    return trips


def performance(
    equity_curve: list[float],
    fills: list[Fill],
    periods_per_year: float,
) -> PerformanceReport:
    if not equity_curve:
        raise ValueError("equity_curve must not be empty")
    start, end = equity_curve[0], equity_curve[-1]
    trips = round_trips(fills)
    wins = [t.pnl for t in trips if t.pnl > 0]
    losses = [-t.pnl for t in trips if t.pnl <= 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    fees = sum(f.fee_quote for f in fills)
    return PerformanceReport(
        start_equity=start,
        end_equity=end,
        total_return_pct=(end - start) / start * 100.0 if start else 0.0,
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        sharpe=sharpe_ratio(equity_curve, periods_per_year),
        trades=len(trips),
        win_rate=len(wins) / len(trips) if trips else 0.0,
        profit_factor=(gross_win / gross_loss)
        if gross_loss > 0
        else (math.inf if gross_win > 0 else 0.0),
        fees_paid=fees,
        fees_pct_of_start_equity=fees / start * 100.0 if start else 0.0,
        round_trips=trips,
    )
