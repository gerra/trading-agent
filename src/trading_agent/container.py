"""Composition root. The ONLY module that imports adapters and wires them into services.

If you want to know "what talks to what", this file is the answer. Everything is built once
from Settings; tests build a Container with fakes via `build_container(settings, **overrides)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog

from trading_agent.adapters.broker.paper_broker import PaperBroker
from trading_agent.adapters.clock import SystemClock
from trading_agent.adapters.market_data.ccxt_market_data import CcxtMarketData, MarketDataRouter
from trading_agent.adapters.market_data.synthetic_market_data import SyntheticMarketData
from trading_agent.adapters.notifier.telegram_notifier import ConsoleNotifier, TelegramNotifier
from trading_agent.adapters.researcher.anthropic_researcher import AnthropicResearcher
from trading_agent.adapters.researcher.static_researcher import StaticResearcher
from trading_agent.adapters.store.candle_store import CandleStore
from trading_agent.adapters.store.jsonl_event_store import JsonlEventStore
from trading_agent.discovery.new_listings import NewListingTracker
from trading_agent.engine.engine import TradingEngine
from trading_agent.engine.risk import RiskManager
from trading_agent.engine.state import EngineStateRepository
from trading_agent.evolution.applier import ProposalApplier
from trading_agent.evolution.context import ContextBuilder
from trading_agent.evolution.loop import EvolutionLoop
from trading_agent.evolution.promotion import PromotionReviewer
from trading_agent.evolution.sandbox import check_source
from trading_agent.evolution.validator import ProposalValidator
from trading_agent.observability.metrics import Metrics
from trading_agent.ports import (
    BrokerPort,
    ClockPort,
    EventStorePort,
    MarketDataPort,
    NotifierPort,
    ResearcherPort,
)
from trading_agent.reporting.daily_report import DailyReportBuilder
from trading_agent.scheduler.jobs import JobRunner
from trading_agent.settings import Settings
from trading_agent.strategies.registry import StrategyRegistry

log = structlog.get_logger(__name__)


@dataclass
class Container:
    settings: Settings
    clock: ClockPort
    metrics: Metrics
    store: EventStorePort
    candle_store: CandleStore
    market_data: MarketDataPort
    broker: BrokerPort
    notifier: NotifierPort
    registry: StrategyRegistry
    state_repo: EngineStateRepository
    listings: NewListingTracker
    engine: TradingEngine
    researcher: ResearcherPort
    evolution: EvolutionLoop
    report: DailyReportBuilder
    job_runner: JobRunner

    def health(self) -> dict[str, Any]:
        state = self.engine.state
        now = self.clock.now()
        age = (now - state.last_tick).total_seconds() if state.last_tick else None
        stale = age is None or age > self.settings.schedule.tick_minutes * 60 * 3
        return {
            "ok": not stale and not state.halted_reason,
            "mode": self.settings.mode,
            "last_tick": state.last_tick,
            "tick_age_s": age,
            "halted": state.halted_reason,
            "sleeves": {k: round(v.cash, 2) for k, v in state.sleeves.items()},
            "reserve_cash": round(state.reserve_cash, 2),
        }


def build_container(settings: Settings, *, synthetic: bool = False, **overrides: Any) -> Container:
    """Build the object graph. `synthetic=True` swaps ccxt for deterministic fake data (demo/tests)."""
    settings.ensure_dirs()
    clock: ClockPort = overrides.get("clock") or SystemClock()
    metrics: Metrics = overrides.get("metrics") or Metrics()
    store: EventStorePort = overrides.get("store") or JsonlEventStore(settings.events_dir)
    candle_store: CandleStore = overrides.get("candle_store") or CandleStore(settings.market_dir)

    market_data: MarketDataPort
    sources: dict[str, MarketDataPort]
    if "market_data" in overrides:
        market_data = overrides["market_data"]
        sources = {market_data.exchange_id: market_data}
    elif synthetic:
        syn = SyntheticMarketData(
            settings.exchange.id, symbols=settings.universe.core_symbols, clock=clock
        )
        market_data, sources = syn, {settings.exchange.id: syn}
    else:
        ids = sorted({settings.exchange.id, *settings.exchange.discovery_exchanges})
        adapters: dict[str, Any] = {}
        for ex in ids:
            if ex not in settings.exchange.fees:
                log.warning("exchange_without_fee_schedule_skipped", exchange=ex)
                continue
            adapters[ex] = CcxtMarketData(ex, settings.exchange.request_timeout_s)
        adapters = {
            settings.exchange.id: adapters.pop(settings.exchange.id),
            **adapters,
        }  # trading exchange first
        market_data, sources = MarketDataRouter(adapters), adapters

    broker: BrokerPort = overrides.get("broker") or PaperBroker(settings.exchange.fees, clock)
    notifier: NotifierPort = overrides.get("notifier") or (
        TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.telegram_bot_token and settings.telegram_chat_id
        else ConsoleNotifier()
    )
    registry = overrides.get("registry") or StrategyRegistry(
        settings.state_dir / "strategy_manifest.json",
        settings.generated_strategies_dir,
        check_source,
    )
    state_repo = overrides.get("state_repo") or EngineStateRepository(
        settings.state_dir / "engine_state.json"
    )
    listings = overrides.get("listings") or NewListingTracker(
        sources, settings.state_dir / "new_listings.json", store, clock
    )
    risk = RiskManager(settings.risk, settings.exchange.fees)

    engine = TradingEngine(
        settings,
        market_data,
        broker,
        store,
        candle_store,
        registry,
        risk,
        listings,
        clock,
        metrics,
        notifier,
        state_repo,
    )

    researcher: ResearcherPort = overrides.get("researcher") or (
        AnthropicResearcher(
            settings.anthropic_api_key,
            settings.evolution.model,
            settings.evolution.effort,
            settings.evolution.web_search_max_uses,
        )
        if settings.anthropic_api_key
        else StaticResearcher()
    )

    def recent_listings() -> (
        list
    ):  # evaluated at validation time so long-running processes stay current
        return listings.recent(
            clock.now(), timedelta(days=settings.universe.new_listing_max_age_days)
        )

    evolution = EvolutionLoop(
        settings,
        ContextBuilder(settings, store, registry, candle_store, clock),
        researcher,
        ProposalValidator(
            settings, registry, candle_store, clock, listings_provider=recent_listings
        ),
        ProposalApplier(settings, registry, clock),
        PromotionReviewer(settings, registry, store),
        store,
        clock,
        metrics,
    )
    report = DailyReportBuilder(settings, store, registry, state_repo)
    job_runner = JobRunner(store, metrics, notifier, clock)
    return Container(
        settings=settings,
        clock=clock,
        metrics=metrics,
        store=store,
        candle_store=candle_store,
        market_data=market_data,
        broker=broker,
        notifier=notifier,
        registry=registry,
        state_repo=state_repo,
        listings=listings,
        engine=engine,
        researcher=researcher,
        evolution=evolution,
        report=report,
        job_runner=job_runner,
    )
