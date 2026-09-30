"""Prometheus metrics. One `Metrics` instance per process; see config/grafana for the dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

if TYPE_CHECKING:
    from trading_agent.core.models import PortfolioSnapshot
    from trading_agent.engine.engine import TickResult


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.equity = Gauge("ta_equity_quote", "Total paper equity (quote ccy)", registry=r)
        self.cash = Gauge("ta_cash_quote", "Total cash incl. reserve", registry=r)
        self.positions_value = Gauge(
            "ta_positions_value_quote", "Mark-to-market value of open positions", registry=r
        )
        self.open_positions = Gauge("ta_open_positions", "Open positions", ["strategy"], registry=r)
        self.sleeve_equity = Gauge(
            "ta_sleeve_equity_quote", "Equity per strategy sleeve", ["strategy"], registry=r
        )
        self.fees_paid = Counter(
            "ta_fees_paid_quote_total", "Cumulative fees paid (this process)", registry=r
        )
        self.fees_total_gauge = Gauge(
            "ta_fees_cumulative_quote", "Cumulative fees paid (from portfolio snapshot)", registry=r
        )
        self.signals = Counter(
            "ta_signals_total", "Signals emitted", ["strategy", "side"], registry=r
        )
        self.decisions = Counter(
            "ta_decisions_total", "Risk decisions by outcome", ["outcome"], registry=r
        )
        self.fills = Counter(
            "ta_fills_total", "Fills", ["strategy", "side", "exchange"], registry=r
        )
        self.breakers = Counter(
            "ta_circuit_breaker_trips_total", "Circuit breaker trips", ["name"], registry=r
        )
        self.errors = Counter("ta_errors_total", "Errors by location", ["where"], registry=r)
        self.tick_duration = Histogram(
            "ta_tick_duration_seconds",
            "Tick wall time",
            registry=r,
            buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
        )
        self.last_tick_ts = Gauge(
            "ta_last_tick_timestamp", "Unix time of last completed tick", registry=r
        )
        self.new_listings = Counter(
            "ta_new_listings_total", "New listings discovered", ["exchange"], registry=r
        )
        self.evolution = Counter(
            "ta_evolution_total", "Evolution loop outcomes", ["outcome"], registry=r
        )
        self.job_runs = Counter(
            "ta_job_runs_total", "Scheduled job runs", ["job", "ok"], registry=r
        )
        self.job_duration = Histogram(
            "ta_job_duration_seconds",
            "Scheduled job duration",
            ["job"],
            registry=r,
            buckets=(1, 5, 15, 60, 300, 900),
        )
        self.llm_calls = Counter(
            "ta_llm_calls_total",
            "Claude API calls made by the evolution loop",
            ["step"],
            registry=r,
        )
        self.halted = Gauge("ta_halted", "1 when the drawdown breaker halted trading", registry=r)

    def observe_snapshot(self, snap: PortfolioSnapshot) -> None:
        self.equity.set(snap.equity)
        self.cash.set(snap.cash)
        self.positions_value.set(snap.positions_value)
        self.fees_total_gauge.set(snap.fees_paid_total)
        for s in snap.sleeves:
            self.sleeve_equity.labels(strategy=s.strategy_id).set(s.equity)
            self.open_positions.labels(strategy=s.strategy_id).set(s.open_positions)

    def observe_tick(self, result: TickResult) -> None:
        self.last_tick_ts.set(result.ts.timestamp())
