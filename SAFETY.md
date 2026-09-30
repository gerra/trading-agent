# Safety

The agent rewrites part of its own code. This file explains the fence around that, so a reader
can verify in minutes that "self-improving" cannot become "self-harming".

## Threat model
1. The LLM emits code that reads secrets, calls the network, deletes files, or spawns processes.
2. The LLM emits code that loops forever or eats memory.
3. A strategy is economically harmful (churns fees, over-concentrates, never exits).
4. A bug in the loop corrupts the manifest or the repository.
5. The operator's server is misused (privilege escalation, persistence, data exfiltration).

## Three layers of defence

**Layer 1 — static policy (`evolution/sandbox.py`)**. Generated modules are parsed with `ast` and
rejected unless they import only from a short allow-list (`math`, `statistics`, `typing`,
`dataclasses`, `datetime`, `collections`, `itertools`, `functools`, `enum`, and three
`trading_agent` modules), define exactly one `Strategy` subclass, use no `eval/exec/open/
getattr/__import__/…`, no dunder attribute access, no nested imports, no async, no
`global`, and stay under a size limit. The same check runs again *every time* the registry
imports a generated file, so editing a file on disk cannot bypass it.

**Layer 2 — sandboxed execution (`evolution/runner.py`, `validator.py`)**. Candidates are
backtested in a separate `python -s -B` subprocess with `RLIMIT_AS`, `RLIMIT_CPU`,
`RLIMIT_NPROC`, `RLIMIT_FSIZE`, a wall-clock timeout, a scrubbed environment (no API keys),
and a temp working directory. The parent process never executes candidate code before the gate
passes; the final in-process import happens only after the file is written and policy-checked.

**Layer 3 — container hardening (`Dockerfile`, `docker-compose.yml`)**. Non-root user, read-only
root filesystem, `cap_drop: ALL`, `no-new-privileges`, memory/CPU limits, writable mounts only
for `data/` and the mutable zone. Optionally restrict egress to the exchanges, Telegram and the
Claude API at the firewall.

## Economic guardrails (`engine/risk.py`, `settings.risk`)
* Entry requires `expected_edge_bps ≥ min_edge_to_cost_multiple × round_trip_cost_bps` (default 2×).
* Per-sleeve caps: position size, open positions, trades per day; no pyramiding; no shorting; spot only.
* Circuit breakers: daily loss, daily fee budget, max drawdown (halts until a human runs `resume`).
* Engine-enforced exits: maximum position age, liquidation of retired sleeves.
* Challengers get a small allocation; promotion needs days of *live* paper outperformance.

## What the loop can and cannot change
| can | cannot |
|---|---|
| add a `.py` file to `strategies/generated/` | touch any other path |
| tune params of an existing strategy (manifest) | change risk limits, fees, settings |
| retire a losing challenger | retire the champion, change allocations of others |
| git-commit its own change with a fixed author | run arbitrary git or shell commands |

LLM calls are budgeted: at most one proposal per `min_days_between_proposals` (default 3), web research at most
every `research_interval_days` (default 7), and no call while a generated challenger is still under live
evaluation (`wait_for_pending_challenger`). Every attempt,
including rejections and errors, is written to `data/events/evolution.jsonl` with the reason.

## Live trading
`settings.mode` is a `Literal["paper"]`. No live broker adapter exists. Going live would need a
new ADR, a `BrokerPort` implementation with explicit human approval flow, and a review of this file.

## If something looks wrong
* `trading-agent status` shows halts and sleeves; `trading-agent check` lints generated code.
* Delete a generated file and set its manifest entry to `retired`; the engine unwinds the sleeve.
* `git log -- src/trading_agent/strategies/generated` is the audit trail of every applied change.
