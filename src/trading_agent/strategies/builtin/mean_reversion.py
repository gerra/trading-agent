"""Short-horizon mean reversion: buy oversold dips in liquid majors, exit on reversion."""

from __future__ import annotations

from typing import Any, ClassVar

from trading_agent.core.models import Signal
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.indicators import rolling_std, rsi, sma


class MeanReversion(Strategy):
    id: ClassVar[str] = "mean_reversion"
    description: ClassVar[str] = (
        "Buy when RSI is oversold and price sits well below its rolling mean; "
        "sell when price returns to the mean, RSI normalises, or a stop is hit."
    )
    default_params: ClassVar[dict[str, Any]] = {
        "rsi_period": 14,
        "rsi_buy": 28.0,
        "rsi_exit": 55.0,
        "mean_period": 40,
        "z_entry": -1.8,
        "stop_pct": 2.5,
    }

    def required_history(self) -> int:
        return int(self.params["mean_period"]) + int(self.params["rsi_period"]) + 5

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out: list[Signal] = []
        p = self.params
        for symbol, candles in ctx.candles.items():
            if len(candles) < self.required_history():
                continue
            closes = [c.close for c in candles]
            r = rsi(closes, int(p["rsi_period"]))[-1]
            mean = sma(closes, int(p["mean_period"]))[-1]
            std = rolling_std(closes, int(p["mean_period"]))[-1]
            if r is None or mean is None or not std:
                continue
            price = closes[-1]
            z = (price - mean) / std
            pos = ctx.positions.get(symbol)
            if pos and pos.is_open:
                if price <= pos.avg_price * (1 - float(p["stop_pct"]) / 100):
                    out.append(self.sell(symbol, "stop loss"))
                elif price >= mean or r >= float(p["rsi_exit"]):
                    out.append(self.sell(symbol, "reverted to mean"))
            elif r <= float(p["rsi_buy"]) and z <= float(p["z_entry"]):
                expected_bps = (mean - price) / price * 10_000
                out.append(
                    self.buy(
                        symbol,
                        strength=0.6,
                        reason=f"oversold rsi={r:.0f} z={z:.1f}",
                        expected_edge_bps=expected_bps,
                    )
                )
        return out
