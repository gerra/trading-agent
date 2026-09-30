"""The strategy contract. Every strategy, hand-written or generated, implements this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from trading_agent.core.models import Candle, NewListing, Position, Side, Signal


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy is allowed to see. Nothing else exists from its point of view."""

    now: datetime
    candles: dict[str, list[Candle]]  # symbol -> closed candles, oldest first
    positions: dict[str, Position]  # this strategy's sleeve only
    round_trip_cost_bps: float  # fees + slippage for a full buy+sell, in bps
    new_listings: list[NewListing] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    def closes(self, symbol: str) -> list[float]:
        return [c.close for c in self.candles.get(symbol, [])]

    def has_position(self, symbol: str) -> bool:
        p = self.positions.get(symbol)
        return bool(p and p.is_open)


class Strategy(ABC):
    """Subclass, set the ClassVars, implement `on_bar`. Keep it pure and deterministic."""

    id: ClassVar[str] = "unnamed"
    description: ClassVar[str] = ""
    default_params: ClassVar[dict[str, Any]] = {}

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = {**self.default_params, **(params or {})}

    def required_history(self) -> int:
        """How many candles per symbol `on_bar` needs to work. Engine guarantees at least this."""
        return 50

    def universe(self, core_symbols: list[str], new_listings: list[NewListing]) -> list[str]:
        """Which symbols this strategy wants candles for. Default: the core universe."""
        return list(core_symbols)

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> list[Signal]: ...

    # --- helpers so generated code stays short and uniform ---
    def buy(self, symbol: str, strength: float, reason: str, expected_edge_bps: float) -> Signal:
        return Signal(
            strategy_id=self.id,
            symbol=symbol,
            side=Side.BUY,
            strength=strength,
            reason=reason,
            expected_edge_bps=expected_edge_bps,
        )

    def sell(self, symbol: str, reason: str) -> Signal:
        return Signal(
            strategy_id=self.id, symbol=symbol, side=Side.SELL, strength=1.0, reason=reason
        )
