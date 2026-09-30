"""Risk manager: the only gate between a Signal and an Order.

Every rejection carries a machine-readable reason. Those reasons are logged as `decision`
events and summarised for the researcher, so the system can learn *why* it is not trading
(e.g. "edge_below_cost" 400 times/day means the strategy is generating uneconomic ideas).
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.core.fees import FeeSchedule, edge_clears_costs
from trading_agent.core.models import Side, Signal, Sleeve
from trading_agent.engine.state import DayStats, EngineState
from trading_agent.settings import RiskSettings


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str
    qty: float = 0.0
    notional_quote: float = 0.0
    round_trip_cost_bps: float | None = None


def exchange_of(instrument: str) -> str:
    """'kraken:BTC/USD' -> 'kraken'."""
    if ":" not in instrument:
        raise ValueError(f"instrument key must be '<exchange>:<BASE>/<QUOTE>', got {instrument!r}")
    return instrument.split(":", 1)[0]


class RiskManager:
    def __init__(self, limits: RiskSettings, fee_schedules: dict[str, FeeSchedule]) -> None:
        self.limits = limits
        self.fee_schedules = fee_schedules

    def schedule_for(self, instrument: str) -> FeeSchedule:
        ex = exchange_of(instrument)
        try:
            return self.fee_schedules[ex]
        except KeyError as e:
            raise KeyError(f"no fee schedule for exchange '{ex}' (instrument {instrument})") from e

    def evaluate(
        self, signal: Signal, sleeve: Sleeve, state: EngineState, price: float
    ) -> Decision:
        schedule = self.schedule_for(signal.symbol)
        rt_cost = schedule.round_trip_cost_bps()
        pos = sleeve.position(signal.symbol)

        if signal.side is Side.SELL:
            if not pos.is_open:
                return Decision(False, "no_position", round_trip_cost_bps=rt_cost)
            return Decision(
                True,
                "exit",
                qty=pos.qty,
                notional_quote=pos.qty * price,
                round_trip_cost_bps=rt_cost,
            )

        # ---- BUY path: cheap global checks first, then per-signal economics ----
        if price <= 0:
            return Decision(False, "bad_price", round_trip_cost_bps=rt_cost)
        if state.halted_reason:
            return Decision(False, f"halted:{state.halted_reason}", round_trip_cost_bps=rt_cost)
        if sleeve.id in state.liquidating:
            return Decision(False, "sleeve_liquidating", round_trip_cost_bps=rt_cost)
        day: DayStats | None = state.day
        if day is not None:
            if day.daily_loss_tripped:
                return Decision(False, "daily_loss_limit", round_trip_cost_bps=rt_cost)
            if day.fee_budget_tripped:
                return Decision(False, "daily_fee_budget", round_trip_cost_bps=rt_cost)
            if day.trades_by_sleeve.get(sleeve.id, 0) >= self.limits.max_trades_per_day_per_sleeve:
                return Decision(False, "trade_rate_limit", round_trip_cost_bps=rt_cost)
        if pos.is_open:
            return Decision(False, "already_in_position", round_trip_cost_bps=rt_cost)
        if len(sleeve.open_symbols()) >= self.limits.max_open_positions_per_sleeve:
            return Decision(False, "max_positions", round_trip_cost_bps=rt_cost)
        if not edge_clears_costs(
            signal.expected_edge_bps, schedule, self.limits.min_edge_to_cost_multiple
        ):
            return Decision(False, "edge_below_cost", round_trip_cost_bps=rt_cost)

        notional = sleeve.cash * self.limits.max_position_pct_of_sleeve * signal.strength
        fee_factor = 1.0 + schedule.taker_bps / 10_000.0
        notional = min(notional, sleeve.cash / fee_factor * 0.999)
        if notional < schedule.min_order_quote:
            return Decision(False, "below_min_order", round_trip_cost_bps=rt_cost)
        qty = notional / price
        return Decision(
            True, "entry", qty=qty, notional_quote=notional, round_trip_cost_bps=rt_cost
        )
