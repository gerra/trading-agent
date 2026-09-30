# Architecture

This document is the map. If you read only one file, read this one.

## 1. Layers (dependencies point down only)

```
 entrypoints   cli.py · scheduler/            "when things run"
 services      engine/ · discovery/ · evolution/ · reporting/   "what the system does"
 strategies    strategies/ (builtin/, generated/, registry.py)  "pluggable signal logic"
 ports         ports/                          "interfaces the services need"
 adapters      adapters/                       "ccxt, paper broker, JSONL files, Telegram, Claude"
 core          core/                           "pure domain: models, fees, accounting, metrics, events"
 composition   container.py · settings.py      "the only place adapters meet services"
 cross-cutting observability/                  "logging, Prometheus metrics, /healthz"
```

Rules:
* `core` imports nothing from the package. `ports` import only `core` (+ evolution schemas).
* Services import `ports`, never `adapters`. `container.py` is the single exception.
* `strategies` import only `core`, `strategies.base`, `strategies.indicators`.
* `evolution` may write to disk only via `applier.py`, and only inside the mutable zone.

## 2. Package map

| package | responsibility | key entry point |
|---|---|---|
| `core/models.py` | Candle, Signal, Order, Fill, Position, Sleeve, snapshots, NewListing | — |
| `core/fees.py` | FeeSchedule, round-trip cost, `edge_clears_costs` | — |
| `core/accounting.py` | apply a Fill to a Sleeve, value sleeves, build PortfolioSnapshot | `apply_fill`, `snapshot` |
| `core/metrics.py` | drawdown, Sharpe, FIFO round trips, PerformanceReport | `performance` |
| `core/events.py` | schemas of every event written to `data/events/` | `*_event()` builders |
| `ports/` | Protocols: MarketData, Broker, EventStore, Notifier, Researcher, Clock | — |
| `adapters/market_data/` | ccxt public data + router by exchange; synthetic data | `CcxtMarketData`, `MarketDataRouter`, `SyntheticMarketData` |
| `adapters/broker/paper_broker.py` | fills at reference ± slippage, taker fee | `PaperBroker.execute` |
| `adapters/store/` | JSONL event store; candle cache | `JsonlEventStore`, `CandleStore` |
| `adapters/notifier/` | Telegram / console / null | `TelegramNotifier.send` |
| `adapters/researcher/` | Claude API (research + structured proposal); offline stub; prompts | `AnthropicResearcher`, `StaticResearcher` |
| `strategies/base.py` | the Strategy contract and StrategyContext | `Strategy.on_bar` |
| `strategies/registry.py` | manifest (status, allocation, params), safe loading of generated code | `StrategyRegistry` |
| `engine/engine.py` | one tick: data → signals → risk → fills → events/state/metrics | `TradingEngine.tick` |
| `engine/risk.py` | all entry/exit gating incl. fee-aware edge check | `RiskManager.evaluate` |
| `engine/state.py` | persistent EngineState (sleeves, reserve, day stats, halts) | `EngineStateRepository` |
| `discovery/new_listings.py` | detect new markets across exchanges | `NewListingTracker.scan` |
| `backtest/simulator.py` | bar-by-bar sim reusing the live risk/broker/accounting | `run_backtest` |
| `evolution/` | the self-improvement loop (see docs/EVOLUTION_LOOP.md) | `EvolutionLoop.run` |
| `reporting/daily_report.py` | ≤10 bullets from the event store | `DailyReportBuilder.build` |
| `observability/` | structlog JSON, Prometheus metrics, health server | `Metrics`, `start_health_server` |
| `scheduler/jobs.py` | APScheduler jobs + JobRunner (timing, events, alerts) | `build_scheduler` |
| `container.py` | builds the object graph from Settings | `build_container` |
| `settings.py` | one typed Settings tree: defaults ← YAML ← env | `load_settings` |

## 3. Data flow of one tick

1. `registry.instantiate_active()` loads strategies from the manifest (generated ones are
   policy-checked before import).
2. `_sync_sleeves` funds sleeves for new manifest entries from `reserve_cash`; retired ones start liquidating.
3. Each strategy declares its universe (`Strategy.universe`); the engine fetches candles for the union,
   drops the still-forming bar, and appends them to the candle cache.
4. Day roll + circuit breakers (`daily_loss`, `fee_budget`, `max_drawdown`).
5. `strategy.on_bar(ctx)` → Signals. Engine adds forced exits (stale positions, retired sleeves).
6. Every Signal → `RiskManager.evaluate` → Decision (logged with reason) → `PaperBroker.execute`
   → `accounting.apply_fill` → events (`signal`, `decision`, `order`, `fill`).
7. `snapshot` event, state saved atomically, Prometheus gauges updated.

## 4. Data flow of the evolution loop

`review live results → build context → research (web) → propose (structured) → validate
(static policy → sandboxed backtest → beat baseline) → apply (write file, manifest, git commit)
→ record`. Details, gate thresholds and failure handling: [docs/EVOLUTION_LOOP.md](docs/EVOLUTION_LOOP.md).

## 5. On-disk layout (`data/`, git-ignored)

```
data/events/{signal,decision,order,fill,snapshot,new_listing,circuit_breaker,evolution,job_run,alert}.jsonl
data/market/<exchange>/<BASE>_<QUOTE>__<tf>.jsonl     candle cache (feeds backtests)
data/state/engine_state.json                          sleeves, reserve, day stats, halt
data/state/strategy_manifest.json                     which strategies, status, allocation, params
data/state/new_listings.json                          per-exchange baselines + listings
data/research/<date>_<proposal>.md                    researcher notes (with sources)
data/logs/agent.jsonl                                 structured logs
src/trading_agent/strategies/generated/*.py           MUTABLE ZONE (git-tracked, committed by the loop)
```

Schemas: [docs/DATA_SCHEMAS.md](docs/DATA_SCHEMAS.md).

## 6. Invariants (tests enforce most of these)

* `settings.mode` can only be `"paper"`; the engine refuses a broker whose `mode` differs.
* A Sleeve can never go cash-negative; a sell can never exceed the held quantity.
* No entry without `expected_edge_bps ≥ min_edge_to_cost_multiple × round_trip_cost_bps`.
* Generated code: only files in the mutable zone, only after `sandbox.check_source` passes,
  only after a subprocess backtest beat the baseline. Never imported without the policy check.
* Backtests execute at the *next* bar's open — no look-ahead.
* Every scheduled job is wrapped: failures become `job_run` events + a rate-limited alert, never a crash.

## 7. Where to change what

| I want to… | touch |
|---|---|
| add an exchange / change fees | `config/settings.yaml` (`exchange.fees`), `core/fees.py` defaults |
| add a hand-written strategy | `strategies/builtin/<name>.py` + an entry in the manifest (or `default_manifest`) |
| change risk limits | `config/settings.yaml` (`risk`) — logic in `engine/risk.py` |
| change what the researcher sees | `evolution/context.py`, prompts in `adapters/researcher/prompts.py` |
| tighten/loosen the code policy | `evolution/sandbox.py` (then update SAFETY.md) |
| change the acceptance bar | `settings.evolution.*` and `evolution/validator.py::_gate` |
| change promotion rules | `evolution/promotion.py` |
| change the daily message | `reporting/daily_report.py` |
| add a metric | `observability/metrics.py` + `config/grafana/dashboards/trading-agent.json` |
| swap the notifier / LLM / data source | new adapter satisfying the port + wiring in `container.py` |
