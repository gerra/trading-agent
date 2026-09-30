from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from trading_agent.adapters.broker.paper_broker import PaperBroker
from trading_agent.adapters.clock import FixedClock
from trading_agent.adapters.market_data.synthetic_market_data import timeframe_seconds
from trading_agent.core import accounting
from trading_agent.core.fees import FeeSchedule
from trading_agent.core.metrics import PerformanceReport, performance
from trading_agent.core.models import Candle, Fill, NewListing, Order, Signal, Sleeve
from trading_agent.engine.risk import RiskManager
from trading_agent.engine.state import DayStats, EngineState
from trading_agent.settings import RiskSettings
from trading_agent.strategies.base import Strategy, StrategyContext


@dataclass
class BacktestResult:
    strategy_id: str
    bars: int
    equity_curve: list[float]
    fills: list[Fill]
    report: PerformanceReport
    signals: int = 0
    rejections: dict[str, int] = field(default_factory=dict)

    def metrics(self) -> dict[str, float | int]:
        return {**self.report.summary(), "bars": self.bars, "signals": self.signals}


def periods_per_year(timeframe: str) -> float:
    return 365 * 24 * 3600 / timeframe_seconds(timeframe)


def run_backtest(
    strategy: Strategy,
    candles_by_symbol: dict[str, list[Candle]],
    risk: RiskSettings,
    fee_schedules: dict[str, FeeSchedule],
    initial_cash: float,
    timeframe: str,
    new_listings: list[NewListing] | None = None,
    window: int = 400,
) -> BacktestResult:
    """Bar-by-bar simulation. Signals from bar i are executed at bar i+1's open (no look-ahead)."""
    symbols = [s for s, cs in candles_by_symbol.items() if cs]
    if not symbols:
        raise ValueError("no candles to backtest on")
    timeline = sorted({c.ts for s in symbols for c in candles_by_symbol[s]})
    warmup = strategy.required_history()
    if len(timeline) <= warmup + 2:
        raise ValueError(f"need more than {warmup + 2} bars, got {len(timeline)}")

    clock = FixedClock(timeline[0])
    broker = PaperBroker(fee_schedules, clock)
    risk_mgr = RiskManager(risk, fee_schedules)
    sleeve = Sleeve(
        id=strategy.id, strategy_id=strategy.id, cash=initial_cash, initial_cash=initial_cash
    )
    state = EngineState(started_at=timeline[0], reserve_cash=0.0, peak_equity=initial_cash)
    listings = new_listings or []
    rt_cost = next(iter(fee_schedules.values())).round_trip_cost_bps()

    idx = {s: 0 for s in symbols}
    fills: list[Fill] = []
    equity_curve: list[float] = []
    pending: list[Signal] = []
    n_signals = 0
    rejections: dict[str, int] = {}

    for bar_index, ts in enumerate(timeline):
        clock.set(ts)
        # advance pointers so history[s] = all candles with c.ts <= ts
        for s in symbols:
            cs = candles_by_symbol[s]
            while idx[s] < len(cs) and cs[idx[s]].ts <= ts:
                idx[s] += 1
        history = {
            s: candles_by_symbol[s][max(0, idx[s] - window) : idx[s]] for s in symbols if idx[s] > 0
        }
        opens = {
            s: candles_by_symbol[s][idx[s] - 1].open
            for s in history
            if candles_by_symbol[s][idx[s] - 1].ts == ts
        }
        closes = {s: h[-1].close for s, h in history.items()}

        day = ts.astimezone(UTC).strftime("%Y-%m-%d")
        if state.day is None or state.day.date != day:
            state.day = DayStats(date=day, start_equity=accounting.sleeve_equity(sleeve, closes))

        # 1) execute signals decided on the previous bar at this bar's open
        for sig in pending:
            price = opens.get(sig.symbol)
            if price is None:
                continue
            decision = risk_mgr.evaluate(sig, sleeve, state, price)
            if not decision.approved:
                rejections[decision.reason] = rejections.get(decision.reason, 0) + 1
                continue
            order = Order(
                id=uuid.uuid4().hex[:12],
                sleeve_id=sleeve.id,
                symbol=sig.symbol,
                side=sig.side,
                qty=decision.qty,
                ts=ts,
                signal_reason=sig.reason,
            )
            fill = broker.execute(order, price)
            sleeve = accounting.apply_fill(sleeve, fill)
            fills.append(fill)
            state.day.fees_paid += fill.fee_quote
            state.day.trades_by_sleeve[sleeve.id] = state.day.trades_by_sleeve.get(sleeve.id, 0) + 1
        pending = []

        # 2) ask the strategy what to do next, given closed bars up to `ts`
        if bar_index >= warmup:
            ctx = StrategyContext(
                now=ts,
                candles=history,
                positions={k: v for k, v in sleeve.positions.items() if v.is_open},
                round_trip_cost_bps=rt_cost,
                new_listings=[n for n in listings if n.first_seen <= ts],
                params=strategy.params,
            )
            out = strategy.on_bar(ctx)
            n_signals += len(out)
            pending.extend(s for s in out if s.strategy_id == strategy.id)

        eq = accounting.sleeve_equity(sleeve, closes)
        equity_curve.append(eq)
        state.peak_equity = max(state.peak_equity, eq)

    report = performance(equity_curve, fills, periods_per_year(timeframe))
    return BacktestResult(
        strategy_id=strategy.id,
        bars=len(timeline),
        equity_curve=equity_curve,
        fills=fills,
        report=report,
        signals=n_signals,
        rejections=rejections,
    )


def now_utc() -> datetime:
    return datetime.now(UTC)
