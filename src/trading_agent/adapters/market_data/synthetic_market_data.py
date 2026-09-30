"""Deterministic synthetic market data for tests, demos and offline backtests.

A regime-switching random walk: long calm trends punctuated by volatility bursts, so both
trend-following and mean-reversion strategies get something to work with.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

from trading_agent.core.models import Candle, Ticker

TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def timeframe_seconds(tf: str) -> int:
    try:
        return TIMEFRAME_SECONDS[tf]
    except KeyError as e:
        raise ValueError(f"unsupported timeframe {tf}") from e


def generate_candles(
    symbol: str,
    n: int,
    timeframe: str = "5m",
    seed: int = 7,
    start_price: float = 100.0,
    end: datetime | None = None,
) -> list[Candle]:
    rng = random.Random(f"{symbol}:{seed}")
    step = timedelta(seconds=timeframe_seconds(timeframe))
    end = end or datetime(2026, 1, 1, tzinfo=UTC)
    secs = timeframe_seconds(timeframe)
    end = datetime.fromtimestamp(
        int(end.timestamp()) // secs * secs, tz=UTC
    )  # align to bar boundary
    t0 = end - step * n
    price = start_price
    drift, vol = 0.0, 0.002
    out: list[Candle] = []
    for i in range(n):
        if i % 120 == 0:
            drift = rng.choice([-0.0004, 0.0, 0.0, 0.0005, 0.0008])
            vol = rng.choice([0.0015, 0.003, 0.006])
        r = rng.gauss(drift, vol)
        o = price
        c = max(0.01, price * math.exp(r))
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        v = abs(rng.gauss(1000, 300)) * (1 + 3 * abs(r) / vol)
        out.append(
            Candle(symbol=symbol, ts=t0 + step * i, open=o, high=hi, low=lo, close=c, volume=v)
        )
        price = c
    return out


class SyntheticMarketData:
    def __init__(
        self,
        exchange_id: str = "kraken",
        seed: int = 7,
        symbols: list[str] | None = None,
        clock=None,
    ) -> None:  # type: ignore[no-untyped-def]
        self._id = exchange_id
        self._seed = seed
        self._symbols = symbols or [
            f"{exchange_id}:BTC/USD",
            f"{exchange_id}:ETH/USD",
            f"{exchange_id}:SOL/USD",
        ]
        self._clock = clock

    @property
    def exchange_id(self) -> str:
        return self._id

    def list_symbols(self) -> list[str]:
        return list(self._symbols)

    def get_candles(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        end = self._clock.now() if self._clock else None
        return generate_candles(instrument, limit, timeframe, self._seed, end=end)

    def get_ticker(self, instrument: str) -> Ticker:
        c = self.get_candles(instrument, "5m", 2)[-1]
        return Ticker(symbol=instrument, ts=c.ts, last=c.close)
