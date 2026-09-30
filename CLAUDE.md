# Working in this repo (humans and AI agents)

## Orientation in 5 minutes
1. Read `ARCHITECTURE.md` (the map) and `SAFETY.md` (the fence).
2. `src/trading_agent/engine/engine.py::TradingEngine.tick` is the whole trading path, top to bottom.
3. `src/trading_agent/evolution/loop.py::EvolutionLoop.run` is the whole self-improvement path.
4. `src/trading_agent/container.py` shows what is wired to what.
5. `tests/` are executable documentation; `tests/test_evolution.py` shows the loop end to end.

## Rules
* **Ports before adapters.** Services depend on `ports/` Protocols. Never import `adapters` from a service.
* **The mutable zone is `strategies/generated/` and nothing else.** Automated code changes go through
  `evolution/applier.py`. Do not add other self-modifying paths.
* **Fees are not optional.** Anything that decides to trade uses `core/fees.py` and passes `RiskManager`.
* **Events are the source of truth.** If you add behaviour, emit an event (`core/events.py`) and
  document it in `docs/DATA_SCHEMAS.md`. The report and the researcher read events, not memory.
* **Never break `mode == "paper"`.** Live trading requires a new ADR, a new broker adapter, a human
  review of `SAFETY.md`, and is out of scope for the automated loop.
* **Keep files small and named for what they do.** Every package has a docstring saying its job.
* **One config tree.** New knobs go in `settings.py` *and* `config/settings.example.yaml`.
* **Secrets only from the environment.** Never in YAML, never in the repo, never in prompts.

## Commands
```bash
pytest                       # must be green before any commit
ruff check src tests         # lint
ruff format src tests        # format
trading-agent --synthetic tick
trading-agent evolve --dry-run
trading-agent check          # policy-lint all generated strategies
```

## Definition of done for a change
* tests added or updated; `pytest` and `ruff check` pass;
* docs updated if architecture, events, settings or safety changed;
* the change is explainable in one paragraph to someone who has only read `ARCHITECTURE.md`.

## What an autonomous agent must not do here
* Widen `evolution/sandbox.py` allow-lists, disable the validator gate, or bypass the applier.
* Add network, filesystem or subprocess access to strategies.
* Store or print API keys, tokens or chat ids.
* Commit `data/` or `config/settings.yaml`.
