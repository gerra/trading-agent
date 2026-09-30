"""Public market data via ccxt. No API keys: paper trading only needs public endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import ccxt

from trading_agent.core.models import Candle, Ticker

STABLE_QUOTES = {"USD", "USDT", "USDC"}


def split_instrument(instrument: str) -> tuple[str, str]:
    """'kraken:BTC/USD' -> ('kraken', 'BTC/USD')."""
    if ":" not in instrument:
        raise ValueError(f"instrument must be '<exchange>:<BASE>/<QUOTE>', got {instrument!r}")
    ex, sym = instrument.split(":", 1)
    return ex, sym


class CcxtMarketData:
    def __init__(self, exchange_id: str, timeout_s: float = 20.0) -> None:
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"unknown ccxt exchange id: {exchange_id}")
        self._id = exchange_id
        self._x = getattr(ccxt, exchange_id)(
            {"enableRateLimit": True, "timeout": int(timeout_s * 1000)}
        )
        self._markets_loaded = False

    @property
    def exchange_id(self) -> str:
        return self._id

    def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            self._x.load_markets()
            self._markets_loaded = True

    def list_symbols(self) -> list[str]:
        self._x.load_markets(True)  # force refresh: discovery needs the newest markets
        self._markets_loaded = True
        out = []
        for sym, m in self._x.markets.items():
            if m.get("spot") and m.get("active", True) and m.get("quote") in STABLE_QUOTES:
                out.append(f"{self._id}:{sym}")
        return sorted(out)

    def get_candles(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        ex, sym = split_instrument(instrument)
        if ex != self._id:
            raise ValueError(f"{instrument} is not on {self._id}")
        self._ensure_markets()
        rows = self._x.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
        candles = [
            Candle(
                symbol=instrument,
                ts=datetime.fromtimestamp(r[0] / 1000, tz=UTC),
                open=r[1],
                high=r[2],
                low=r[3],
                close=r[4],
                volume=r[5] or 0.0,
            )
            for r in rows
            if r[4] is not None
        ]
        # The last row is usually the still-forming candle; drop it so strategies only see closed bars.
        return candles[:-1] if len(candles) > 1 else candles

    def get_ticker(self, instrument: str) -> Ticker:
        ex, sym = split_instrument(instrument)
        self._ensure_markets()
        t = self._x.fetch_ticker(sym)
        return Ticker(
            symbol=instrument,
            ts=datetime.now(UTC),
            last=float(t["last"]),
            bid=t.get("bid"),
            ask=t.get("ask"),
            quote_volume_24h=t.get("quoteVolume"),
        )


class MarketDataRouter:
    """Routes '<exchange>:<symbol>' instrument keys to the right per-exchange adapter."""

    def __init__(self, adapters: dict[str, CcxtMarketData | object]) -> None:
        self._adapters = adapters
        self._default = next(iter(adapters))

    @property
    def exchange_id(self) -> str:
        return self._default

    def adapter(self, instrument: str):  # type: ignore[no-untyped-def]
        ex, _ = split_instrument(instrument)
        try:
            return self._adapters[ex]
        except KeyError as e:
            raise KeyError(f"no market data adapter for exchange '{ex}'") from e

    def list_symbols(self) -> list[str]:
        return self._adapters[self._default].list_symbols()  # type: ignore[attr-defined]

    def get_candles(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        return self.adapter(instrument).get_candles(instrument, timeframe, limit)

    def get_ticker(self, instrument: str) -> Ticker:
        return self.adapter(instrument).get_ticker(instrument)
