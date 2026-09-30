# Runbook

## First-time setup
1. **Telegram**: talk to `@BotFather` → `/newbot` → copy the token. Send your bot a message, then
   open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
2. **Claude API key**: create one in the Anthropic console. Without it the loop runs offline (no proposals).
3. `cp .env.example .env` and fill in the three values. `cp config/settings.example.yaml config/settings.yaml`.
4. `docker compose up -d --build`. Check `docker compose logs -f agent` for `starting` and a `tick` line,
   and `curl localhost:9100/healthz`.

## Daily operation
* 22:00 Europe/London: Telegram summary (≤10 bullets). Equity, positions, trades/fees, best/worst
  strategy, new listings, what evolution applied/rejected and why, breakers, top rejection reason.
* 03:17 UTC: evolution run. Notes land in `data/research/`, outcomes in `data/events/evolution.jsonl`
  and `git log -- src/trading_agent/strategies/generated`.
* Grafana at `:3000` for everything else.

## Recovering
| symptom | action |
|---|---|
| `🛑 HALTED` in the report / `ta_halted=1` | inspect `trading-agent status`, decide, then `trading-agent resume` |
| a generated strategy misbehaves | set its manifest status to `retired` (or delete the file and the entry); the engine sells out its sleeve |
| exchange errors in logs | ticks continue with the symbols that work; check `ta_errors_total{where="market_data"}` |
| evolution `error` outcomes | usually API/key/rate-limit; see `data/events/evolution.jsonl` reason and `job_run` events |
| want to start over | stop the agent, delete `data/state/` (keeps events and candles), start again |

## Backups
`data/` is the whole memory of the system. Back up `data/events`, `data/state`, `data/market` and
the git repo (which contains generated strategies and their history).

## Upgrading the code
`docker compose build && docker compose up -d`. State files are versioned by pydantic models; add
fields with defaults to stay backward compatible.
