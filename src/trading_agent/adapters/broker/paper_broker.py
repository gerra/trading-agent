"""Paper broker: fills market orders at reference price +/- slippage, charges taker fees.

Deliberately pessimistic (always taker, always adverse slippage). If a strategy is not
profitable here it will not be profitable live.
"""

from __future__ import annotations

from trading_agent.core.fees import FeeSchedule
from trading_agent.core.models import Fill, Order, Side
from trading_agent.ports import ClockPort


class PaperBroker:
    mode = "paper"

    def __init__(self, fee_schedules: dict[str, FeeSchedule], clock: ClockPort) -> None:
        self._fees = fee_schedules
        self._clock = clock

    def execute(self, order: Order, reference_price: float) -> Fill:
        exchange = order.symbol.split(":", 1)[0]
        schedule = self._fees[exchange]
        slip = schedule.slippage_bps / 10_000.0
        price = (
            reference_price * (1 + slip) if order.side is Side.BUY else reference_price * (1 - slip)
        )
        notional = order.qty * price
        return Fill(
            order_id=order.id,
            sleeve_id=order.sleeve_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=price,
            fee_quote=schedule.fee_for(notional, taker=True),
            slippage_bps=schedule.slippage_bps,
            ts=self._clock.now(),
        )
