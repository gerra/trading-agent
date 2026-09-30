from __future__ import annotations

from typing import Protocol

from trading_agent.core.models import Candle, Ticker


class MarketDataPort(Protocol):
    """Read-only access to an exchange's public market data."""

    @property
    def exchange_id(self) -> str: ...

    def list_symbols(self) -> list[str]:
        """All active spot symbols like 'BTC/USDT'. Used by new-listing discovery."""
        ...

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """Most recent `limit` closed candles, oldest first."""
        ...

    def get_ticker(self, symbol: str) -> Ticker: ...
