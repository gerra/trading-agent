"""New-listing momentum: trade coins that just appeared on an exchange.

Thesis: large, regulated desks are slow to onboard new assets, so the first days after a
listing can carry a momentum / attention premium. The flip side is brutal volatility and
thin books, so this strategy is deliberately strict: it waits for a few bars of history,
requires a real up-move on rising volume, sizes small, and always carries a stop and a
maximum holding time.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from trading_agent.core.models import NewListing, Signal
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.indicators import pct_change


class NewListingMomentum(Strategy):
    id: ClassVar[str] = "new_listing_momentum"
    description: ClassVar[str] = (
        "Buy recently listed coins after a confirmed up-move on rising volume; "
        "trailing stop, hard stop and time-based exit."
    )
    default_params: ClassVar[dict[str, Any]] = {
        "min_bars": 24,
        "lookback_bars": 12,
        "min_move_pct": 4.0,
        "volume_ratio_min": 1.3,
        "stop_pct": 6.0,
        "trailing_pct": 5.0,
        "max_hold_hours": 48,
        "max_listing_age_days": 10,
    }

    def required_history(self) -> int:
        return int(self.params["min_bars"])

    def universe(self, core_symbols: list[str], new_listings: list[NewListing]) -> list[str]:
        # This strategy ignores the core universe and only looks at fresh listings.
        return sorted({n.symbol for n in new_listings})

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        p = self.params
        out: list[Signal] = []
        max_age = timedelta(days=float(p["max_listing_age_days"]))
        fresh = {n.symbol for n in ctx.new_listings if ctx.now - n.first_seen <= max_age}

        for symbol, candles in ctx.candles.items():
            pos = ctx.positions.get(symbol)
            if pos and pos.is_open:
                price = candles[-1].close if candles else pos.avg_price
                high_since_entry = max(
                    (c.high for c in candles if pos.opened_at and c.ts >= pos.opened_at),
                    default=price,
                )
                held = ctx.now - pos.opened_at if pos.opened_at else timedelta(0)
                if price <= pos.avg_price * (1 - float(p["stop_pct"]) / 100):
                    out.append(self.sell(symbol, "hard stop"))
                elif price <= high_since_entry * (1 - float(p["trailing_pct"]) / 100):
                    out.append(self.sell(symbol, "trailing stop"))
                elif held >= timedelta(hours=float(p["max_hold_hours"])):
                    out.append(self.sell(symbol, "max holding time"))
                continue

            if symbol not in fresh or len(candles) < int(p["min_bars"]):
                continue
            closes = [c.close for c in candles]
            move = pct_change(closes, int(p["lookback_bars"]))
            lb = int(p["lookback_bars"])
            recent_vol = sum(c.volume for c in candles[-lb:]) / lb
            prior = candles[-2 * lb : -lb]
            prior_vol = (sum(c.volume for c in prior) / len(prior)) if prior else 0.0
            vol_ratio = recent_vol / prior_vol if prior_vol > 0 else 0.0
            if (
                move is not None
                and move * 100 >= float(p["min_move_pct"])
                and vol_ratio >= float(p["volume_ratio_min"])
            ):
                expected = move * 10_000 * 0.5  # assume half the observed move continues
                out.append(
                    self.buy(
                        symbol,
                        strength=0.5,
                        reason=f"new listing +{move * 100:.1f}% vol x{vol_ratio:.1f}",
                        expected_edge_bps=expected,
                    )
                )
        return out
