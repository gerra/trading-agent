# Data schemas

All event files are JSONL: one `{"kind", "ts", "payload"}` per line (`core/events.py`).
Timestamps are ISO-8601 UTC. Money is in the quote currency (USD-equivalent stablecoins).

| kind | payload | written by |
|---|---|---|
| `signal` | `Signal`: strategy_id, symbol, side, strength, reason, expected_edge_bps | engine |
| `decision` | `DecisionRecord`: strategy_id, sleeve_id, symbol, side, approved, reason, qty, notional_quote, expected_edge_bps, round_trip_cost_bps | engine |
| `order` | `Order`: id, sleeve_id, symbol, side, qty, type, ts, signal_reason | engine |
| `fill` | `Fill`: order_id, sleeve_id, symbol, side, qty, price, fee_quote, slippage_bps, ts | engine |
| `snapshot` | `PortfolioSnapshot`: ts, equity, cash, positions_value, fees_paid_total, sleeves[] | engine |
| `new_listing` | `NewListing`: exchange, symbol, base, quote, first_seen | discovery |
| `circuit_breaker` | `CircuitBreakerRecord`: name, tripped, detail | engine |
| `evolution` | `EvolutionRecord`: proposal_id, kind, strategy_id, outcome, reason, rationale, hypothesis, metrics, baseline_metrics, file_path, commit | evolution |
| `job_run` | `JobRunRecord`: job, ok, duration_s, error | scheduler |
| `alert` | level, message | (reserved) |

Instrument keys are `<exchange>:<BASE>/<QUOTE>`, e.g. `kraken:BTC/USD`.

## State files (`data/state/`)
* `engine_state.json` — `EngineState`: started_at, sleeves{id → Sleeve}, reserve_cash, peak_equity,
  day{date, start_equity, fees_paid, trades_by_sleeve, *_tripped}, halted_reason, liquidating[], last_tick, ticks.
* `strategy_manifest.json` — `Manifest.entries[]`: id, origin(builtin|generated), module, class_name,
  status(champion|challenger|retired), allocation_pct (initial funding), params, since, version, proposal_id, notes.
* `new_listings.json` — per-exchange `known[]` + `baseline_at`, and `listings[]`.

## Candle cache (`data/market/<exchange>/<BASE>_<QUOTE>__<tf>.jsonl`)
`Candle`: symbol, ts, open, high, low, close, volume — deduped by ts on read, append-only on write.

## Analysing offline
```python
import pandas as pd, json
fills = pd.read_json("data/events/fill.jsonl", lines=True)
fills = pd.json_normalize(fills["payload"]).assign(ts=pd.to_datetime(fills["ts"]))
```
