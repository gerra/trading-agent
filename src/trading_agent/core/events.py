"""Event schemas for the append-only event store.

Every meaningful thing the agent does becomes one of these records in `data/events/*.jsonl`.
They are the ground truth for the daily report, the evolution loop's context, offline
analysis and any future model training. Add new event kinds here and document them in
docs/DATA_SCHEMAS.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from trading_agent.core.models import Fill, NewListing, Order, PortfolioSnapshot, Signal

EventKind = Literal[
    "signal",
    "decision",
    "order",
    "fill",
    "snapshot",
    "new_listing",
    "circuit_breaker",
    "evolution",
    "job_run",
    "alert",
]


class Event(BaseModel):
    kind: EventKind
    ts: datetime
    payload: dict[str, Any]


class DecisionRecord(BaseModel):
    """Risk manager verdict on a signal - the 'why did / didn't we trade' record."""

    strategy_id: str
    sleeve_id: str
    symbol: str
    side: str
    approved: bool
    reason: str
    qty: float = 0.0
    notional_quote: float = 0.0
    expected_edge_bps: float | None = None
    round_trip_cost_bps: float | None = None


class CircuitBreakerRecord(BaseModel):
    name: str
    tripped: bool
    detail: str


class EvolutionRecord(BaseModel):
    """One iteration of the self-improvement loop: what was proposed and what happened to it."""

    proposal_id: str
    kind: str
    strategy_id: str
    outcome: Literal[
        "applied", "rejected", "rolled_back", "promoted", "demoted", "no_change", "error"
    ]
    reason: str
    rationale: str = ""
    hypothesis: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    baseline_metrics: dict[str, Any] = Field(default_factory=dict)
    file_path: str | None = None
    commit: str | None = None


class JobRunRecord(BaseModel):
    job: str
    ok: bool
    duration_s: float
    error: str | None = None


def signal_event(ts: datetime, s: Signal) -> Event:
    return Event(kind="signal", ts=ts, payload=s.model_dump(mode="json"))


def decision_event(ts: datetime, d: DecisionRecord) -> Event:
    return Event(kind="decision", ts=ts, payload=d.model_dump(mode="json"))


def order_event(ts: datetime, o: Order) -> Event:
    return Event(kind="order", ts=ts, payload=o.model_dump(mode="json"))


def fill_event(ts: datetime, f: Fill) -> Event:
    return Event(kind="fill", ts=ts, payload=f.model_dump(mode="json"))


def snapshot_event(s: PortfolioSnapshot) -> Event:
    return Event(kind="snapshot", ts=s.ts, payload=s.model_dump(mode="json"))


def new_listing_event(ts: datetime, n: NewListing) -> Event:
    return Event(kind="new_listing", ts=ts, payload=n.model_dump(mode="json"))


def circuit_breaker_event(ts: datetime, c: CircuitBreakerRecord) -> Event:
    return Event(kind="circuit_breaker", ts=ts, payload=c.model_dump(mode="json"))


def evolution_event(ts: datetime, e: EvolutionRecord) -> Event:
    return Event(kind="evolution", ts=ts, payload=e.model_dump(mode="json"))


def job_run_event(ts: datetime, j: JobRunRecord) -> Event:
    return Event(kind="job_run", ts=ts, payload=j.model_dump(mode="json"))


def alert_event(ts: datetime, level: str, message: str) -> Event:
    return Event(kind="alert", ts=ts, payload={"level": level, "message": message})
