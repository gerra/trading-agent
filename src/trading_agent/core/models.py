"""Domain models shared by every layer.

All money amounts are in the *quote* currency of the traded pair (e.g. USDT for BTC/USDT)
unless a field name says otherwise. All timestamps are timezone-aware UTC datetimes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Candle(BaseModel):
    """One OHLCV bar."""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class Ticker(BaseModel):
    symbol: str
    ts: datetime
    last: float
    bid: float | None = None
    ask: float | None = None
    quote_volume_24h: float | None = None


class NewListing(BaseModel):
    """A market that appeared on an exchange since the previous discovery scan."""

    exchange: str
    symbol: str
    base: str
    quote: str
    first_seen: datetime


class Signal(BaseModel):
    """What a strategy wants to do. Strategies emit signals; they never place orders."""

    strategy_id: str
    symbol: str
    side: Side
    strength: float = Field(ge=0.0, le=1.0, description="0..1 conviction, scales position size")
    reason: str = Field(max_length=300)
    expected_edge_bps: float | None = Field(
        default=None,
        description="Strategy's own estimate of expected gross move in bps; risk compares it to costs",
    )


class Order(BaseModel):
    id: str
    sleeve_id: str
    symbol: str
    side: Side
    qty: float = Field(gt=0, description="Base-asset quantity")
    type: Literal["market"] = "market"
    ts: datetime
    signal_reason: str = ""


class Fill(BaseModel):
    order_id: str
    sleeve_id: str
    symbol: str
    side: Side
    qty: float
    price: float
    fee_quote: float
    slippage_bps: float
    ts: datetime

    @property
    def notional(self) -> float:
        return self.qty * self.price


class Position(BaseModel):
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    opened_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.qty > 1e-12


class Sleeve(BaseModel):
    """A virtual sub-portfolio owned by exactly one strategy.

    Keeping capital per strategy makes champion/challenger comparison trivial and keeps a bad
    generated strategy from touching the whole book.
    """

    id: str
    strategy_id: str
    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    initial_cash: float

    @model_validator(mode="after")
    def _non_negative(self) -> Sleeve:
        if self.cash < -1e-9:
            raise ValueError(f"sleeve {self.id} cash went negative: {self.cash}")
        return self

    def position(self, symbol: str) -> Position:
        return self.positions.get(symbol) or Position(symbol=symbol)

    def open_symbols(self) -> list[str]:
        return [s for s, p in self.positions.items() if p.is_open]


class SleeveSnapshot(BaseModel):
    sleeve_id: str
    strategy_id: str
    equity: float
    cash: float
    positions_value: float
    realized_pnl: float
    fees_paid: float
    open_positions: int


class PortfolioSnapshot(BaseModel):
    ts: datetime
    equity: float
    cash: float
    positions_value: float
    fees_paid_total: float
    sleeves: list[SleeveSnapshot]
