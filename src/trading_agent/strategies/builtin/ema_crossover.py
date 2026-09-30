"""Trend-following EMA crossover with a volatility-aware edge estimate."""

from __future__ import annotations

from typing import Any, ClassVar

from trading_agent.core.models import Signal
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.indicators import atr, ema


class EmaCrossover(Strategy):
    id: ClassVar[str] = "ema_crossover"
    description: ClassVar[str] = (
        "Buy when fast EMA crosses above slow EMA and recent ATR implies the move can cover costs; "
        "sell on the opposite cross or a protective stop."
    )
    default_params: ClassVar[dict[str, Any]] = {
        "fast": 12,
        "slow": 48,
        "atr_period": 14,
        "atr_edge_multiple": 3.0,
        "stop_atr_multiple": 2.0,
    }

    def required_history(self) -> int:
        return int(self.params["slow"]) + int(self.params["atr_period"]) + 5

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        signals: list[Signal] = []
        p = self.params
        for symbol, candles in ctx.candles.items():
            if len(candles) < self.required_history():
                continue
            closes = [c.close for c in candles]
            fast = ema(closes, int(p["fast"]))
            slow = ema(closes, int(p["slow"]))
            atr_vals = atr(
                [c.high for c in candles], [c.low for c in candles], closes, int(p["atr_period"])
            )
            if None in (fast[-1], fast[-2], slow[-1], slow[-2], atr_vals[-1]):
                continue
            f_now, f_prev = float(fast[-1]), float(fast[-2])  # type: ignore[arg-type]
            s_now, s_prev = float(slow[-1]), float(slow[-2])  # type: ignore[arg-type]
            price = closes[-1]
            atr_bps = float(atr_vals[-1]) / price * 10_000  # type: ignore[arg-type]
            crossed_up = f_prev <= s_prev and f_now > s_now
            crossed_down = f_prev >= s_prev and f_now < s_now
            pos = ctx.positions.get(symbol)

            if pos and pos.is_open:
                stop = pos.avg_price - float(p["stop_atr_multiple"]) * float(atr_vals[-1])  # type: ignore[arg-type]
                if crossed_down:
                    signals.append(self.sell(symbol, "fast EMA crossed below slow EMA"))
                elif price < stop:
                    signals.append(self.sell(symbol, f"stop hit ({price:.4g} < {stop:.4g})"))
            elif crossed_up:
                expected = atr_bps * float(p["atr_edge_multiple"])
                signals.append(
                    self.buy(
                        symbol,
                        strength=0.8,
                        reason="fast EMA crossed above slow EMA",
                        expected_edge_bps=expected,
                    )
                )
        return signals
