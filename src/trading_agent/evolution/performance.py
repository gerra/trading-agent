"""Per-strategy live performance from the event store. Shared by context, promotion and report."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from pydantic import BaseModel, Field

from trading_agent.core.metrics import max_drawdown_pct, round_trips
from trading_agent.core.models import Fill
from trading_agent.ports import EventStorePort


class StrategyPerformance(BaseModel):
    strategy_id: str
    status: str = ""
    allocation_pct: float = 0.0
    params: dict = Field(default_factory=dict)
    equity_start: float | None = None
    equity_now: float | None = None
    return_pct: float | None = None
    max_drawdown_pct: float | None = None
    trades: int = 0
    win_rate: float | None = None
    fees_paid: float = 0.0
    open_positions: int = 0
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    signals: int = 0


def sleeve_equity_series(
    store: EventStorePort, strategy_id: str, since: datetime | None, until: datetime | None = None
) -> list[tuple[datetime, float]]:
    out = []
    for ev in store.read("snapshot", since=since, until=until):
        for s in ev.payload.get("sleeves", []):
            if s.get("strategy_id") == strategy_id:
                out.append((ev.ts, float(s["equity"])))
    return out


def portfolio_equity_series(
    store: EventStorePort, since: datetime | None, until: datetime | None = None
) -> list[tuple[datetime, float]]:
    return [
        (ev.ts, float(ev.payload["equity"]))
        for ev in store.read("snapshot", since=since, until=until)
    ]


def strategy_performance(
    store: EventStorePort, strategy_id: str, since: datetime | None, until: datetime | None = None
) -> StrategyPerformance:
    perf = StrategyPerformance(strategy_id=strategy_id)
    series = sleeve_equity_series(store, strategy_id, since, until)
    if series:
        perf.equity_start, perf.equity_now = series[0][1], series[-1][1]
        if perf.equity_start:
            perf.return_pct = (perf.equity_now - perf.equity_start) / perf.equity_start * 100
        perf.max_drawdown_pct = max_drawdown_pct([e for _, e in series])
    fills = [
        Fill.model_validate(ev.payload)
        for ev in store.read("fill", since=since, until=until)
        if ev.payload.get("sleeve_id") == strategy_id
    ]
    trips = round_trips(fills)
    perf.trades = len(trips)
    if trips:
        perf.win_rate = sum(1 for t in trips if t.pnl > 0) / len(trips)
    perf.fees_paid = sum(f.fee_quote for f in fills)
    rejected = Counter(
        ev.payload["reason"]
        for ev in store.read("decision", since=since, until=until)
        if ev.payload.get("strategy_id") == strategy_id and not ev.payload.get("approved")
    )
    perf.rejection_reasons = dict(rejected.most_common(6))
    perf.signals = sum(
        1
        for ev in store.read("signal", since=since, until=until)
        if ev.payload.get("strategy_id") == strategy_id
    )
    last_snap = None
    for ev in store.read("snapshot", since=since, until=until):
        last_snap = ev
    if last_snap:
        for s in last_snap.payload.get("sleeves", []):
            if s.get("strategy_id") == strategy_id:
                perf.open_positions = int(s.get("open_positions", 0))
    return perf
