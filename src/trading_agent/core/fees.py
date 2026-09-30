"""Fee and cost model.

The single most common way a retail bot loses money is churning: paying the spread + fees on
every round trip while the edge per trade is smaller than the cost. Everything that decides
whether to trade must go through `RoundTripCost` so that fees are impossible to forget.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FeeSchedule(BaseModel):
    """Per-exchange trading costs, in basis points (1 bp = 0.01%)."""

    exchange: str
    maker_bps: float = Field(ge=0)
    taker_bps: float = Field(ge=0)
    slippage_bps: float = Field(ge=0, description="Assumed adverse price move on market orders")
    min_order_quote: float = Field(ge=0, description="Exchange minimum order size in quote ccy")

    def round_trip_cost_bps(self, taker: bool = True) -> float:
        """Total cost of buying and later selling, as bps of notional."""
        fee = self.taker_bps if taker else self.maker_bps
        return 2.0 * (fee + self.slippage_bps)

    def fee_for(self, notional_quote: float, taker: bool = True) -> float:
        fee_bps = self.taker_bps if taker else self.maker_bps
        return notional_quote * fee_bps / 10_000.0


# Conservative public retail tiers (lowest volume tier, mid-2026). Kept deliberately
# pessimistic; a real account is usually cheaper, never more expensive.
DEFAULT_FEE_SCHEDULES: dict[str, FeeSchedule] = {
    "kraken": FeeSchedule(
        exchange="kraken", maker_bps=25, taker_bps=40, slippage_bps=10, min_order_quote=5
    ),
    "coinbase": FeeSchedule(
        exchange="coinbase", maker_bps=40, taker_bps=60, slippage_bps=10, min_order_quote=1
    ),
    "binance": FeeSchedule(
        exchange="binance", maker_bps=10, taker_bps=10, slippage_bps=8, min_order_quote=5
    ),
    "bitstamp": FeeSchedule(
        exchange="bitstamp", maker_bps=30, taker_bps=40, slippage_bps=10, min_order_quote=10
    ),
    "gateio": FeeSchedule(
        exchange="gateio", maker_bps=20, taker_bps=20, slippage_bps=15, min_order_quote=3
    ),
    "mexc": FeeSchedule(
        exchange="mexc", maker_bps=0, taker_bps=5, slippage_bps=20, min_order_quote=1
    ),
}


def edge_clears_costs(
    expected_edge_bps: float | None, schedule: FeeSchedule, multiple: float
) -> bool:
    """A trade is only worth taking when its expected edge exceeds `multiple` x round-trip cost."""
    if expected_edge_bps is None:
        return False
    return expected_edge_bps >= multiple * schedule.round_trip_cost_bps()
