"""Portfolio accounting: how fills change a sleeve, and how a sleeve is valued.

Pure functions. The engine calls these; the paper broker never mutates sleeves itself.
"""

from __future__ import annotations

from datetime import datetime

from trading_agent.core.models import (
    Fill,
    PortfolioSnapshot,
    Position,
    Side,
    Sleeve,
    SleeveSnapshot,
)


def apply_fill(sleeve: Sleeve, fill: Fill) -> Sleeve:
    """Return a new Sleeve with the fill applied (immutable style, easy to test)."""
    if fill.sleeve_id != sleeve.id:
        raise ValueError(f"fill for sleeve {fill.sleeve_id} applied to sleeve {sleeve.id}")
    positions = dict(sleeve.positions)
    pos = positions.get(fill.symbol) or Position(symbol=fill.symbol)
    cash = sleeve.cash
    realized = sleeve.realized_pnl

    if fill.side is Side.BUY:
        cost = fill.notional + fill.fee_quote
        if cost > cash + 1e-9:
            raise ValueError(
                f"insufficient cash in sleeve {sleeve.id}: need {cost:.2f}, have {cash:.2f}"
            )
        new_qty = pos.qty + fill.qty
        new_avg = (pos.qty * pos.avg_price + fill.notional) / new_qty
        positions[fill.symbol] = Position(
            symbol=fill.symbol, qty=new_qty, avg_price=new_avg, opened_at=pos.opened_at or fill.ts
        )
        cash -= cost
    else:
        if fill.qty > pos.qty + 1e-9:
            raise ValueError(f"cannot sell {fill.qty} of {fill.symbol}; sleeve holds {pos.qty}")
        proceeds = fill.notional - fill.fee_quote
        realized += (fill.price - pos.avg_price) * fill.qty - fill.fee_quote
        remaining = pos.qty - fill.qty
        if remaining <= 1e-12:
            positions[fill.symbol] = Position(symbol=fill.symbol)
        else:
            positions[fill.symbol] = Position(
                symbol=fill.symbol, qty=remaining, avg_price=pos.avg_price, opened_at=pos.opened_at
            )
        cash += proceeds

    return sleeve.model_copy(
        update={
            "cash": cash,
            "positions": positions,
            "realized_pnl": realized,
            "fees_paid": sleeve.fees_paid + fill.fee_quote,
        }
    )


def positions_value(sleeve: Sleeve, prices: dict[str, float]) -> float:
    total = 0.0
    for symbol, pos in sleeve.positions.items():
        if pos.is_open:
            price = prices.get(symbol, pos.avg_price)
            total += pos.qty * price
    return total


def sleeve_equity(sleeve: Sleeve, prices: dict[str, float]) -> float:
    return sleeve.cash + positions_value(sleeve, prices)


def snapshot(ts: datetime, sleeves: list[Sleeve], prices: dict[str, float]) -> PortfolioSnapshot:
    sleeve_snaps: list[SleeveSnapshot] = []
    for s in sleeves:
        pv = positions_value(s, prices)
        sleeve_snaps.append(
            SleeveSnapshot(
                sleeve_id=s.id,
                strategy_id=s.strategy_id,
                equity=s.cash + pv,
                cash=s.cash,
                positions_value=pv,
                realized_pnl=s.realized_pnl,
                fees_paid=s.fees_paid,
                open_positions=len(s.open_symbols()),
            )
        )
    return PortfolioSnapshot(
        ts=ts,
        equity=sum(x.equity for x in sleeve_snaps),
        cash=sum(x.cash for x in sleeve_snaps),
        positions_value=sum(x.positions_value for x in sleeve_snaps),
        fees_paid_total=sum(x.fees_paid for x in sleeve_snaps),
        sleeves=sleeve_snaps,
    )
