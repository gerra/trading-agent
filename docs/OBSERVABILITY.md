# Observability

## Logs
Structured JSON via structlog to stdout and `data/logs/agent.jsonl`. Key events: `tick`, `fill`,
`circuit_breaker`, `strategy_error`, `evolution`, `job_failed`, `llm_usage`, `new_listing`.
`--pretty-logs` renders them for humans.

## Metrics (Prometheus, `GET :9100/metrics`)
| metric | type | labels | meaning |
|---|---|---|---|
| `ta_equity_quote` | gauge | | total paper equity |
| `ta_cash_quote`, `ta_positions_value_quote` | gauge | | cash incl. reserve / MTM of positions |
| `ta_sleeve_equity_quote` | gauge | strategy | equity per strategy sleeve |
| `ta_open_positions` | gauge | strategy | open positions per sleeve |
| `ta_fees_paid_quote_total` | counter | | fees paid since process start |
| `ta_fees_cumulative_quote` | gauge | | cumulative fees from state |
| `ta_signals_total` | counter | strategy, side | signals emitted |
| `ta_decisions_total` | counter | outcome | approved / rejection reason |
| `ta_fills_total` | counter | strategy, side, exchange | fills |
| `ta_circuit_breaker_trips_total` | counter | name | breaker trips |
| `ta_errors_total` | counter | where | caught errors |
| `ta_tick_duration_seconds` | histogram | | tick wall time |
| `ta_last_tick_timestamp` | gauge | | staleness check |
| `ta_new_listings_total` | counter | exchange | listings discovered |
| `ta_evolution_total` | counter | outcome | evolution outcomes |
| `ta_llm_calls_total` | counter | step | Claude API calls (research / propose) |
| `ta_job_runs_total`, `ta_job_duration_seconds` | counter/histogram | job(,ok) | scheduler jobs |
| `ta_halted` | gauge | | 1 when drawdown-halted |

## Health (`GET :9100/healthz`)
`{"ok": bool, "last_tick": ..., "tick_age_s": ..., "halted": ..., "sleeves": {...}, "reserve_cash": ...}`;
503 when the last tick is older than 3 intervals or trading is halted.

## Dashboard
`docker compose up` starts Prometheus (`:9090`) and Grafana (`:3000`, admin/admin) with the
provisioned dashboard `config/grafana/dashboards/trading-agent.json`: equity, sleeves, fees vs
PnL, decisions by reason, breaker trips, evolution outcomes, tick latency, job health.

## Alerts (Telegram)
Circuit breaker trips, job failures (rate-limited to one per job per hour), the start-up message,
and the 22:00 daily report. Prometheus alert rules for staleness are in `config/prometheus/alerts.yml`.
