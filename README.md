# trading-agent

A self-improving **paper-trading** crypto agent. It trades a simulated account on real market
data, hunts for newly listed coins, researches better approaches every day with an LLM, rewrites
its own strategy code inside a strictly sandboxed zone, and sends you a 10-bullet Telegram
summary every evening.

> Paper only. There is deliberately no live-trading code path (see [SAFETY.md](SAFETY.md)).

## 60-second mental model

```
                 every 15 min                      every 30 min
   ┌──────────────────────────────┐      ┌────────────────────────────┐
   │ TradingEngine.tick()         │      │ NewListingTracker.scan()   │
   │  candles → strategies →      │◄─────│  diff exchange market      │
   │  RiskManager → PaperBroker → │      │  lists → new_listing events│
   │  events + state + metrics    │      └────────────────────────────┘
   └──────────────┬───────────────┘
                  │ data/events/*.jsonl  (append-only ground truth)
                  ▼
   ┌──────────────────────────────┐      ┌────────────────────────────┐
   │ EvolutionLoop.run()  (daily) │      │ DailyReportBuilder (22:00) │
   │  review live results →       │      │  ≤10 bullets → Telegram    │
   │  research (weekly) → propose │      └────────────────────────────┘
   │  sandbox backtest → apply    │
   │  into strategies/generated/  │
   └──────────────────────────────┘
```

* **Strategies are pure functions** of candles → signals. They cannot touch money, files or network.
* **Built-in strategies are low-turnover by design**: Donchian breakout trend-following (champion), time-series
  momentum with volatility targeting, new-listing momentum, and a mean-reversion challenger. Fast moving-average
  crossovers are deliberately absent: on 15-minute bars they fire on noise and pay the round-trip cost every time.
* **Every trade passes the RiskManager**, which knows the exchange's fees and rejects any entry
  whose expected edge does not clear a multiple of the round-trip cost. Fees cannot be forgotten.
* **Capital is split into sleeves**, one per strategy, so champion vs challenger is a fair fight
  and a bad generated strategy can only lose its own sleeve.
* **The LLM is called sparingly**: one web-research call per week, one proposal call every few days, and none at all
  while a freshly added strategy is still being judged on live results (`evolution.*` in settings).
* **The evolution loop can only write to one directory** (`src/trading_agent/strategies/generated/`)
  and only code that passes an AST allow-list, a resource-limited backtest and a "beat the baseline"
  gate. Every attempt, applied or not, is recorded with its reasoning.
* **Everything is observable**: JSON logs, Prometheus `/metrics`, `/healthz`, a Grafana dashboard,
  and Telegram alerts on circuit breakers and job failures.

## Quick start (local, no keys needed)

```bash
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"     # or: pip install -e ".[dev]"
cp config/settings.example.yaml config/settings.yaml               # edit to taste
trading-agent --synthetic tick          # one tick on deterministic fake data
trading-agent backfill                  # pull real candles (public endpoints, no keys)
trading-agent tick                      # one real paper tick
trading-agent report                    # print today's summary
trading-agent run                       # scheduler: tick / discover / evolve / report
pytest                                  # full offline test suite
```

Add secrets to `.env` (see `.env.example`): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` for reports,
`ANTHROPIC_API_KEY` for the research loop. Without the API key the loop still runs (promotion
review + ledger) but proposes no changes.

## Run it for real (Docker)

```bash
cp .env.example .env && $EDITOR .env
docker compose up -d --build            # agent + Prometheus + Grafana (http://localhost:3000)
docker compose logs -f agent
```

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for keys, operations and recovery.

## CLI

| command | what it does |
|---|---|
| `run` | start everything (scheduler + `/metrics` + `/healthz` on port 9100) |
| `tick` | one trading tick |
| `discover` | one new-listing scan |
| `evolve [--dry-run]` | one self-improvement iteration |
| `report [--send]` | build (and send) the daily summary |
| `backtest --strategy ID` | backtest a manifest strategy on stored candles |
| `backfill [--bars N]` | download candle history into `data/market/` |
| `status` / `resume` / `check` | inspect state / clear a drawdown halt / lint generated strategies |

Global flags: `--config PATH`, `--synthetic` (no network), `--pretty-logs`.

## Where to read next

* [ARCHITECTURE.md](ARCHITECTURE.md) — the map: layers, packages, data flow, invariants, "where to change what".
* [AGENTS.md](AGENTS.md) — rules for humans and AI agents modifying this repo.
* [SAFETY.md](SAFETY.md) — what the self-improving part can and cannot do, and why.
* [docs/EVOLUTION_LOOP.md](docs/EVOLUTION_LOOP.md) — the self-improvement loop step by step.
* [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md), [docs/DATA_SCHEMAS.md](docs/DATA_SCHEMAS.md), [docs/adr/](docs/adr/).

## Exchange choice (UK)

Paper trading only needs **public** market data, so no account is required. The default fee model
is Kraken's (available to UK residents, FCA-registered, with a solid public API); new-listing
discovery additionally scans Binance, Coinbase and Gate.io public market lists. Fees per exchange
live in `config/settings.yaml` and are deliberately pessimistic.
