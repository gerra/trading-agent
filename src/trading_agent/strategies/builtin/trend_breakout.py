"""Donchian channel breakout with a volatility filter and a chandelier trailing stop.

Why this and not a moving-average crossover: crossovers fire on every wiggle and pay the
round-trip cost each time. A channel breakout only enters when price makes a new N-bar high,
which in crypto happens a few times a month per major, and it rides the whole move with a
trailing stop. Fewer, larger trades is the only way a fee-paying retail account survives.
"""

from __future__ import annotations

from typing import Any, ClassVar

from trading_agent.core.models import Signal
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.indicators import atr


class TrendBreakout(Strategy):
    id: ClassVar[str] = "trend_breakout"
    description: ClassVar[str] = (
        "Buy a close above the prior N-bar high when volatility is high enough to pay for the trade; "
        "exit on a close below the prior M-bar low or a chandelier (ATR) trailing stop."
    )
    default_params: ClassVar[dict[str, Any]] = {
        "channel_bars": 192,  # 48h of 15m bars
        "exit_bars": 48,  # 12h low as the trailing exit
        "atr_period": 14,
        "atr_stop_multiple": 2.5,
        "min_atr_bps": 25.0,  # skip dead markets: one ATR must be worth a quarter of a percent
        "edge_atr_multiple": 4.0,  # a breakout is expected to travel ~4 ATR before the exit triggers
    }

    def required_history(self) -> int:
        return int(self.params["channel_bars"]) + int(self.params["atr_period"]) + 5

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        p = self.params
        n, m = int(p["channel_bars"]), int(p["exit_bars"])
        out: list[Signal] = []
        for symbol, candles in ctx.candles.items():
            if len(candles) < self.required_history():
                continue
            closes = [c.close for c in candles]
            highs = [c.high for c in candles]
            lows = [c.low for c in candles]
            atr_now = atr(highs, lows, closes, int(p["atr_period"]))[-1]
            if atr_now is None:
                continue
            price = closes[-1]
            atr_bps = float(atr_now) / price * 10_000
            pos = ctx.positions.get(symbol)

            if pos and pos.is_open:
                since_entry = [
                    c for c in candles if pos.opened_at and c.ts >= pos.opened_at
                ] or candles[-1:]
                chandelier = max(c.high for c in since_entry) - float(
                    p["atr_stop_multiple"]
                ) * float(atr_now)
                exit_low = min(lows[-m - 1 : -1])
                if price < exit_low:
                    out.append(self.sell(symbol, f"close below {m}-bar low"))
                elif price < chandelier:
                    out.append(self.sell(symbol, "chandelier stop"))
                continue

            channel_high = max(highs[-n - 1 : -1])  # prior bars only, never the current one
            if price > channel_high and atr_bps >= float(p["min_atr_bps"]):
                out.append(
                    self.buy(
                        symbol,
                        strength=0.8,
                        reason=f"close {price:.4g} above {n}-bar high {channel_high:.4g}",
                        expected_edge_bps=atr_bps * float(p["edge_atr_multiple"]),
                    )
                )
        return out
