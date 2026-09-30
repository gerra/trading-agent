"""TradingEngine: one `tick()` = fetch data, ask strategies, gate by risk, execute, record.

Read top to bottom; there is no hidden control flow. Everything the tick does is emitted as
events (signal, decision, order, fill, snapshot, circuit_breaker) so it can be replayed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from trading_agent.adapters.store.candle_store import CandleStore
from trading_agent.core import accounting
from trading_agent.core.events import (
    CircuitBreakerRecord,
    DecisionRecord,
    circuit_breaker_event,
    decision_event,
    fill_event,
    order_event,
    signal_event,
    snapshot_event,
)
from trading_agent.core.models import Candle, Order, PortfolioSnapshot, Side, Signal, Sleeve
from trading_agent.discovery.new_listings import NewListingTracker
from trading_agent.engine.risk import RiskManager, exchange_of
from trading_agent.engine.state import DayStats, EngineState, EngineStateRepository
from trading_agent.observability.metrics import Metrics
from trading_agent.ports import BrokerPort, ClockPort, EventStorePort, MarketDataPort, NotifierPort
from trading_agent.settings import Settings
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.registry import StrategyRegistry

log = structlog.get_logger(__name__)


@dataclass
class TickResult:
    ts: datetime
    snapshot: PortfolioSnapshot
    signals: int = 0
    fills: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        market_data: MarketDataPort,
        broker: BrokerPort,
        store: EventStorePort,
        candle_store: CandleStore,
        registry: StrategyRegistry,
        risk: RiskManager,
        listings: NewListingTracker,
        clock: ClockPort,
        metrics: Metrics,
        notifier: NotifierPort,
        state_repo: EngineStateRepository,
    ) -> None:
        if broker.mode != settings.mode:
            raise RuntimeError(
                f"broker mode '{broker.mode}' != settings.mode '{settings.mode}' - refusing to start"
            )
        self.s = settings
        self.market_data = market_data
        self.broker = broker
        self.store = store
        self.candle_store = candle_store
        self.registry = registry
        self.risk = risk
        self.listings = listings
        self.clock = clock
        self.metrics = metrics
        self.notifier = notifier
        self.state_repo = state_repo
        self.state: EngineState = self.state_repo.load() or self._fresh_state()

    # ------------------------------------------------------------------ public
    def tick(self) -> TickResult:
        now = self.clock.now()
        with self.metrics.tick_duration.time():
            result = self._tick(now)
        self.metrics.observe_tick(result)
        return result

    def resume(self) -> None:
        """Clear a drawdown halt after a human looked at it."""
        self.state.halted_reason = None
        self.state.peak_equity = 0.0
        self.state_repo.save(self.state)

    # ----------------------------------------------------------------- private
    def _fresh_state(self) -> EngineState:
        now = self.clock.now()
        return EngineState(
            started_at=now, reserve_cash=self.s.risk.initial_capital_quote, peak_equity=0.0
        )

    def _tick(self, now: datetime) -> TickResult:
        errors: list[str] = []
        strategies = self.registry.instantiate_active()
        self._sync_sleeves(strategies, now)

        new_listings = self.listings.recent(
            now, max_age=timedelta(days=self.s.universe.new_listing_max_age_days)
        )
        new_listings = new_listings[: self.s.universe.max_new_listings_tracked]
        symbols_by_strategy = {
            sid: strat.universe(self.s.universe.core_symbols, new_listings)
            for sid, strat in strategies.items()
        }
        wanted = sorted(
            {sym for syms in symbols_by_strategy.values() for sym in syms} | self._held_symbols()
        )
        candles = self._fetch_candles(wanted, errors)
        prices = {sym: cs[-1].close for sym, cs in candles.items() if cs}

        self._roll_day(now, prices)
        self._check_breakers(now, prices)
        self.metrics.halted.set(1 if self.state.halted_reason else 0)

        signals: list[Signal] = []
        for sid, strat in strategies.items():
            sleeve = self.state.sleeves[sid]
            ctx = StrategyContext(
                now=now,
                candles={sym: candles[sym] for sym in symbols_by_strategy[sid] if sym in candles},
                positions={k: v for k, v in sleeve.positions.items() if v.is_open},
                round_trip_cost_bps=self.risk.schedule_for(
                    self.s.universe.core_symbols[0]
                ).round_trip_cost_bps(),
                new_listings=new_listings,
                params=strat.params,
            )
            try:
                out = strat.on_bar(ctx)
            except Exception as exc:  # noqa: BLE001 - strategy bugs must never kill the tick
                errors.append(f"{sid}: {exc}")
                self.metrics.errors.labels(where=f"strategy:{sid}").inc()
                log.error("strategy_error", strategy_id=sid, error=str(exc))
                continue
            for sig in out:
                if sig.strategy_id != sid:
                    errors.append(
                        f"{sid} emitted signal with strategy_id={sig.strategy_id}; dropped"
                    )
                    continue
                signals.append(sig)
        signals.extend(self._forced_exits(now, prices))

        rejected: dict[str, int] = {}
        fills = 0
        for sig in signals:
            self.store.append(signal_event(now, sig))
            self.metrics.signals.labels(strategy=sig.strategy_id, side=sig.side.value).inc()
            sleeve = self.state.sleeves.get(sig.strategy_id)
            price = prices.get(sig.symbol)
            if sleeve is None or price is None:
                rejected["no_price_or_sleeve"] = rejected.get("no_price_or_sleeve", 0) + 1
                continue
            decision = self.risk.evaluate(sig, sleeve, self.state, price)
            self.store.append(
                decision_event(
                    now,
                    DecisionRecord(
                        strategy_id=sig.strategy_id,
                        sleeve_id=sleeve.id,
                        symbol=sig.symbol,
                        side=sig.side.value,
                        approved=decision.approved,
                        reason=decision.reason,
                        qty=decision.qty,
                        notional_quote=decision.notional_quote,
                        expected_edge_bps=sig.expected_edge_bps,
                        round_trip_cost_bps=decision.round_trip_cost_bps,
                    ),
                )
            )
            self.metrics.decisions.labels(
                outcome="approved" if decision.approved else decision.reason
            ).inc()
            if not decision.approved:
                rejected[decision.reason] = rejected.get(decision.reason, 0) + 1
                continue
            try:
                self._execute(sig, sleeve, decision.qty, price, now)
                fills += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"execute {sig.symbol}: {exc}")
                self.metrics.errors.labels(where="execute").inc()
                log.error("execute_failed", symbol=sig.symbol, error=str(exc))

        snap = accounting.snapshot(now, list(self.state.sleeves.values()), prices)
        snap = snap.model_copy(
            update={
                "cash": snap.cash + self.state.reserve_cash,
                "equity": snap.equity + self.state.reserve_cash,
            }
        )
        self.store.append(snapshot_event(snap))
        self.state.peak_equity = max(self.state.peak_equity, snap.equity)
        self.state.last_tick = now
        self.state.ticks += 1
        self._finish_liquidations()
        self.state_repo.save(self.state)
        self.metrics.observe_snapshot(snap)
        log.info(
            "tick",
            equity=round(snap.equity, 2),
            signals=len(signals),
            fills=fills,
            rejected=rejected,
            errors=len(errors),
        )
        return TickResult(
            ts=now,
            snapshot=snap,
            signals=len(signals),
            fills=fills,
            rejected=rejected,
            errors=errors,
        )

    def _sync_sleeves(self, strategies: dict[str, Strategy], now: datetime) -> None:
        """Create sleeves for new manifest entries, start liquidating sleeves of retired ones."""
        for entry in self.registry.active_entries():
            if entry.id in strategies and entry.id not in self.state.sleeves:
                want = self.s.risk.initial_capital_quote * entry.allocation_pct / 100.0
                cash = min(want, self.state.reserve_cash)
                if cash <= 0:
                    log.warning("no_reserve_for_sleeve", strategy_id=entry.id)
                    cash = 0.0
                self.state.reserve_cash -= cash
                self.state.sleeves[entry.id] = Sleeve(
                    id=entry.id, strategy_id=entry.id, cash=cash, initial_cash=cash
                )
                log.info("sleeve_created", strategy_id=entry.id, cash=cash)
        active_ids = {e.id for e in self.registry.active_entries()}
        for sid in list(self.state.sleeves):
            if sid not in active_ids and sid not in self.state.liquidating:
                self.state.liquidating.append(sid)
                log.info("sleeve_liquidating", strategy_id=sid)

    def _finish_liquidations(self) -> None:
        for sid in list(self.state.liquidating):
            sleeve = self.state.sleeves.get(sid)
            if sleeve is None:
                self.state.liquidating.remove(sid)
                continue
            if not sleeve.open_symbols():
                self.state.reserve_cash += sleeve.cash
                del self.state.sleeves[sid]
                self.state.liquidating.remove(sid)
                log.info("sleeve_closed", strategy_id=sid, returned_cash=sleeve.cash)

    def _held_symbols(self) -> set[str]:
        return {sym for s in self.state.sleeves.values() for sym in s.open_symbols()}

    def _fetch_candles(self, symbols: list[str], errors: list[str]) -> dict[str, list[Candle]]:
        out: dict[str, list[Candle]] = {}
        tf, limit = self.s.universe.timeframe, self.s.universe.candle_history
        for sym in symbols:
            try:
                cs = self.market_data.get_candles(sym, tf, limit)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"candles {sym}: {exc}")
                self.metrics.errors.labels(where="market_data").inc()
                log.warning("candles_failed", symbol=sym, error=str(exc))
                continue
            if not cs:
                continue
            out[sym] = cs
            try:
                self.candle_store.append(sym, tf, cs)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"candle_store {sym}: {exc}")
        return out

    def _roll_day(self, now: datetime, prices: dict[str, float]) -> None:
        today = now.astimezone(UTC).strftime("%Y-%m-%d")
        if self.state.day is None or self.state.day.date != today:
            equity = (
                accounting.snapshot(now, list(self.state.sleeves.values()), prices).equity
                + self.state.reserve_cash
            )
            self.state.day = DayStats(date=today, start_equity=equity)

    def _check_breakers(self, now: datetime, prices: dict[str, float]) -> None:
        day = self.state.day
        assert day is not None
        equity = (
            accounting.snapshot(now, list(self.state.sleeves.values()), prices).equity
            + self.state.reserve_cash
        )
        changes: list[CircuitBreakerRecord] = []

        if day.start_equity > 0 and not day.daily_loss_tripped:
            loss_pct = (day.start_equity - equity) / day.start_equity * 100
            if loss_pct >= self.s.risk.max_daily_loss_pct:
                day.daily_loss_tripped = True
                changes.append(
                    CircuitBreakerRecord(
                        name="daily_loss",
                        tripped=True,
                        detail=f"down {loss_pct:.2f}% today; new entries blocked until tomorrow",
                    )
                )
        if equity > 0 and not day.fee_budget_tripped:
            fee_pct = day.fees_paid / equity * 100
            if fee_pct >= self.s.risk.max_daily_fees_pct_of_equity:
                day.fee_budget_tripped = True
                changes.append(
                    CircuitBreakerRecord(
                        name="fee_budget",
                        tripped=True,
                        detail=f"fees today {fee_pct:.2f}% of equity; new entries blocked until tomorrow",
                    )
                )
        if self.state.peak_equity > 0 and not self.state.halted_reason:
            dd = (self.state.peak_equity - equity) / self.state.peak_equity * 100
            if dd >= self.s.risk.max_drawdown_pct:
                self.state.halted_reason = f"drawdown {dd:.2f}% >= {self.s.risk.max_drawdown_pct}%"
                changes.append(
                    CircuitBreakerRecord(
                        name="max_drawdown",
                        tripped=True,
                        detail=f"{self.state.halted_reason}; HALTED until `trading-agent resume`",
                    )
                )

        for c in changes:
            self.store.append(circuit_breaker_event(now, c))
            self.metrics.breakers.labels(name=c.name).inc()
            log.warning("circuit_breaker", name=c.name, detail=c.detail)
            self.notifier.send(f"⚠️ circuit breaker: {c.name} - {c.detail}")

    def _forced_exits(self, now: datetime, prices: dict[str, float]) -> list[Signal]:
        """Safety exits the engine enforces regardless of what strategies say."""
        out: list[Signal] = []
        max_age = timedelta(hours=self.s.risk.max_position_age_hours)
        for sleeve in self.state.sleeves.values():
            for sym in sleeve.open_symbols():
                if sym not in prices:
                    continue
                pos = sleeve.position(sym)
                if sleeve.id in self.state.liquidating:
                    out.append(
                        Signal(
                            strategy_id=sleeve.strategy_id,
                            symbol=sym,
                            side=Side.SELL,
                            strength=1,
                            reason="engine: sleeve retired",
                        )
                    )
                elif pos.opened_at and now - pos.opened_at > max_age:
                    out.append(
                        Signal(
                            strategy_id=sleeve.strategy_id,
                            symbol=sym,
                            side=Side.SELL,
                            strength=1,
                            reason="engine: max position age",
                        )
                    )
        return out

    def _execute(
        self, sig: Signal, sleeve: Sleeve, qty: float, price: float, now: datetime
    ) -> None:
        order = Order(
            id=uuid.uuid4().hex[:12],
            sleeve_id=sleeve.id,
            symbol=sig.symbol,
            side=sig.side,
            qty=qty,
            ts=now,
            signal_reason=sig.reason,
        )
        self.store.append(order_event(now, order))
        fill = self.broker.execute(order, price)
        self.state.sleeves[sleeve.id] = accounting.apply_fill(sleeve, fill)
        self.store.append(fill_event(now, fill))
        assert self.state.day is not None
        self.state.day.fees_paid += fill.fee_quote
        self.state.day.trades_by_sleeve[sleeve.id] = (
            self.state.day.trades_by_sleeve.get(sleeve.id, 0) + 1
        )
        self.metrics.fills.labels(
            strategy=sleeve.strategy_id, side=fill.side.value, exchange=exchange_of(fill.symbol)
        ).inc()
        self.metrics.fees_paid.inc(fill.fee_quote)
        log.info(
            "fill",
            strategy=sleeve.strategy_id,
            symbol=fill.symbol,
            side=fill.side.value,
            qty=fill.qty,
            price=fill.price,
            fee=fill.fee_quote,
            reason=sig.reason,
        )
