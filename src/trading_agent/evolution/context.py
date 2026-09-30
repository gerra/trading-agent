"""ResearchContext: everything the researcher is allowed to know, assembled from the event store.

Design rule: the LLM sees *summaries and source code*, never secrets, never raw file paths
outside the repo, never the ability to run anything. It reads; the validator decides.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from trading_agent.adapters.store.candle_store import CandleStore
from trading_agent.evolution.performance import (
    StrategyPerformance,
    portfolio_equity_series,
    strategy_performance,
)
from trading_agent.evolution.sandbox import POLICY_DESCRIPTION
from trading_agent.ports import ClockPort, EventStorePort
from trading_agent.settings import Settings
from trading_agent.strategies.registry import StrategyRegistry

SRC_ROOT = Path(__file__).resolve().parents[1]


class PastProposal(BaseModel):
    id: str
    ts: datetime
    kind: str
    strategy_id: str
    outcome: str
    reason: str
    hypothesis: str = ""


class ResearchContext(BaseModel):
    generated_at: datetime
    lookback_days: int
    portfolio_equity: float | None
    portfolio_start_equity: float | None
    portfolio_return_pct: float | None
    fees_paid_total: float
    round_trip_cost_bps: float
    fee_schedule_text: str
    strategies: list[StrategyPerformance]
    recent_listings: list[str]
    market_summary: list[str]
    past_proposals: list[PastProposal]
    policy: str = POLICY_DESCRIPTION
    strategy_interface_source: str
    indicators_source: str
    example_strategy_source: str
    generated_sources: dict[str, str] = Field(default_factory=dict)
    risk_limits: dict = Field(default_factory=dict)

    def performance_of(self, strategy_id: str) -> StrategyPerformance | None:
        return next((s for s in self.strategies if s.strategy_id == strategy_id), None)


class ContextBuilder:
    def __init__(
        self,
        settings: Settings,
        store: EventStorePort,
        registry: StrategyRegistry,
        candle_store: CandleStore,
        clock: ClockPort,
        lookback_days: int = 7,
    ) -> None:
        self.s = settings
        self.store = store
        self.registry = registry
        self.candles = candle_store
        self.clock = clock
        self.lookback_days = lookback_days

    def build(self) -> ResearchContext:
        now = self.clock.now()
        since = now - timedelta(days=self.lookback_days)
        series = portfolio_equity_series(self.store, since=None)
        eq_now = series[-1][1] if series else None
        eq_start = series[0][1] if series else None
        fees_total = 0.0
        last_snap = list(self.store.read("snapshot"))[-1:] if series else []
        if last_snap:
            fees_total = float(last_snap[0].payload.get("fees_paid_total", 0.0))

        strategies: list[StrategyPerformance] = []
        for entry in self.registry.manifest.entries:
            perf = strategy_performance(self.store, entry.id, since=since)
            perf.status, perf.allocation_pct, perf.params = (
                entry.status,
                entry.allocation_pct,
                entry.params,
            )
            strategies.append(perf)

        listings = [
            ev.payload["symbol"]
            for ev in self.store.read(
                "new_listing", since=now - timedelta(days=self.s.universe.new_listing_max_age_days)
            )
        ]
        past = [
            PastProposal(
                id=ev.payload["proposal_id"],
                ts=ev.ts,
                kind=ev.payload["kind"],
                strategy_id=ev.payload["strategy_id"],
                outcome=ev.payload["outcome"],
                reason=ev.payload["reason"][:300],
                hypothesis=ev.payload.get("hypothesis", "")[:200],
            )
            for ev in self.store.read("evolution")
        ][-25:]

        fee = self.s.fee_schedule()
        generated = {}
        for entry in self.registry.manifest.entries:
            if entry.origin == "generated":
                p = self.s.generated_strategies_dir / entry.module
                if p.exists():
                    generated[entry.id] = p.read_text()[:8000]

        return ResearchContext(
            generated_at=now,
            lookback_days=self.lookback_days,
            portfolio_equity=eq_now,
            portfolio_start_equity=eq_start,
            portfolio_return_pct=((eq_now - eq_start) / eq_start * 100)
            if eq_now and eq_start
            else None,
            fees_paid_total=fees_total,
            round_trip_cost_bps=fee.round_trip_cost_bps(),
            fee_schedule_text=f"{fee.exchange}: maker {fee.maker_bps}bps taker {fee.taker_bps}bps slippage {fee.slippage_bps}bps, round trip {fee.round_trip_cost_bps():.0f}bps, min order {fee.min_order_quote}",
            strategies=strategies,
            recent_listings=sorted(set(listings))[:30],
            market_summary=self._market_summary(),
            past_proposals=past,
            strategy_interface_source=(SRC_ROOT / "strategies" / "base.py").read_text(),
            indicators_source=(SRC_ROOT / "strategies" / "indicators.py").read_text(),
            example_strategy_source=(
                SRC_ROOT / "strategies" / "builtin" / "ema_crossover.py"
            ).read_text(),
            generated_sources=generated,
            risk_limits=self.s.risk.model_dump(),
        )

    def _market_summary(self) -> list[str]:
        out = []
        tf = self.s.universe.timeframe
        for sym in self.candles.instruments(tf)[:40]:
            cs = self.candles.read(sym, tf)
            if len(cs) < 10:
                continue
            last = cs[-1].close
            day_bars = int(86400 / _tf_seconds(tf))
            ref = cs[-day_bars].close if len(cs) > day_bars else cs[0].close
            chg = (last - ref) / ref * 100 if ref else 0.0
            out.append(f"{sym}: last {last:.6g}, {len(cs)} bars stored, {chg:+.1f}% vs ~24h ago")
        return out


def _tf_seconds(tf: str) -> int:
    from trading_agent.adapters.market_data.synthetic_market_data import timeframe_seconds

    return timeframe_seconds(tf)
