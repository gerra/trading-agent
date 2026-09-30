"""Time-series momentum with volatility targeting.

The most robust anomaly in the crypto literature: an asset that went up over the last day or
so tends to keep going for a while. Position size is scaled by inverse realised volatility so
a quiet market gets a full position and a violent one gets a small one. A minimum holding time
and a hysteresis band between entry and exit thresholds keep turnover (and fees) low.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, ClassVar

from trading_agent.core.models import Signal
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.indicators import atr, pct_change


class TimeSeriesMomentum(Strategy):
    id: ClassVar[str] = "tsmom"
    description: ClassVar[str] = (
        "Buy when 24h and 72h returns are both positive and the 24h return exceeds a threshold; "
        "size by inverse volatility; exit when 24h momentum turns negative, on a 3-ATR stop, "
        "or after a maximum hold."
    )
    default_params: ClassVar[dict[str, Any]] = {
        "fast_bars": 96,  # 24h of 15m bars
        "slow_bars": 288,  # 72h
        "entry_pct": 1.5,
        "exit_pct": -0.5,
        "target_daily_vol_pct": 3.0,
        "min_hold_hours": 6,
        "max_hold_hours": 96,
        "atr_period": 14,
        "atr_stop_multiple": 3.0,
    }

    def required_history(self) -> int:
        return int(self.params["slow_bars"]) + 5

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        p = self.params
        fast, slow = int(p["fast_bars"]), int(p["slow_bars"])
        out: list[Signal] = []
        for symbol, candles in ctx.candles.items():
            if len(candles) < self.required_history():
                continue
            closes = [c.close for c in candles]
            r_fast = pct_change(closes, fast)
            r_slow = pct_change(closes, slow)
            if r_fast is None or r_slow is None:
                continue
            price = closes[-1]
            pos = ctx.positions.get(symbol)

            if pos and pos.is_open:
                held = ctx.now - pos.opened_at if pos.opened_at else timedelta(0)
                atr_now = atr(
                    [c.high for c in candles],
                    [c.low for c in candles],
                    closes,
                    int(p["atr_period"]),
                )[-1]
                stop = pos.avg_price - float(p["atr_stop_multiple"]) * float(atr_now or 0.0)
                if price < stop:
                    out.append(self.sell(symbol, "ATR stop"))
                elif held >= timedelta(hours=float(p["max_hold_hours"])):
                    out.append(self.sell(symbol, "max hold"))
                elif held >= timedelta(hours=float(p["min_hold_hours"])) and r_fast * 100 <= float(
                    p["exit_pct"]
                ):
                    out.append(
                        self.sell(symbol, f"24h momentum {r_fast * 100:+.1f}% turned negative")
                    )
                continue

            if r_fast * 100 >= float(p["entry_pct"]) and r_slow > 0:
                daily_vol = _daily_vol_pct(closes[-fast:], bars_per_day=fast)
                strength = (
                    1.0
                    if daily_vol <= 0
                    else min(1.0, max(0.3, float(p["target_daily_vol_pct"]) / daily_vol))
                )
                out.append(
                    self.buy(
                        symbol,
                        strength=strength,
                        reason=f"24h {r_fast * 100:+.1f}% 72h {r_slow * 100:+.1f}% vol {daily_vol:.1f}%/d",
                        expected_edge_bps=abs(r_fast)
                        * 10_000
                        * 0.5,  # assume half the move continues
                    )
                )
        return out


def _daily_vol_pct(closes: list[float], bars_per_day: int) -> float:
    rets = [
        math.log(b / a) for a, b in zip(closes[:-1], closes[1:], strict=False) if a > 0 and b > 0
    ]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(bars_per_day) * 100
