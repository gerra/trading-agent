# The evolution loop

`EvolutionLoop.run()` (`src/trading_agent/evolution/loop.py`) runs once a day (03:17 UTC by default)
and on demand via `trading-agent evolve [--dry-run]`.

```mermaid
flowchart TD
    A[1. PromotionReviewer.review\nlive paper results ≥ N days] --> B{daily proposal\nbudget left?}
    B -- no --> Z[done]
    B -- yes --> C[2. ContextBuilder.build\nperformance, rejections, code,\npast attempts, market summary]
    C --> D[3. Researcher.research\nClaude + web search → markdown notes\nsaved to data/research/]
    D --> E[4. Researcher.propose\nstructured ProposalDraft:\nnew_strategy | tune_params | retire | no_change]
    E --> F[5. ProposalValidator.validate]
    F --> F1[static policy\nsandbox.check_source]
    F1 --> F2[subprocess backtest\nrlimits + timeout, same data for\ncandidate and baseline]
    F2 --> F3{gate:\ntrades ≥ min, DD ≤ max,\nreturn > 0, beats baseline\nby margin, fees ≤ cap}
    F3 -- fail --> R[record: rejected + reason]
    F3 -- pass --> G[6. ProposalApplier.apply\nwrite generated/<id>.py,\nmanifest challenger entry,\ngit commit]
    G --> H[record: applied + metrics]
    G -- exception --> RB[rollback file + manifest\nrecord: rolled_back]
```

## Step details

1. **Review** (`promotion.py`) — no LLM. For each challenger older than `promotion_min_days`,
   compare its sleeve return over that window with the champion's. Promote if it wins by
   `promote_margin_pct` with at least `min_live_trades_for_promotion` trades; retire if it lost
   `retire_loss_pct` or trails by `retire_margin_pct`. Retiring makes the engine sell out the sleeve
   and return cash to the reserve.
2. **Context** (`context.py`) — everything the LLM may see: per-strategy live performance and
   rejection reasons, portfolio stats, fee schedule, risk limits, recent listings, stored-market
   summary, the last 25 proposals with outcomes, the strategy contract, indicators, one example
   strategy, current generated sources, and the policy text.
3. **Research** (`adapters/researcher/anthropic_researcher.py`) — one Claude call with the
   `web_search` server tool. Output is free-form markdown saved under `data/research/`.
4. **Propose** — a second call with structured output (`ProposalDraft`). Exactly one proposal.
5. **Validate** (`validator.py`) — see gate in the diagram; thresholds live in `settings.evolution`.
   Candidate and baseline (champion, or the same strategy with current params) are run through
   `evolution/runner.py` on the same stored candles (`data/market/`).
6. **Apply** (`applier.py`) — the only writer to the mutable zone and the manifest. New strategies
   start as `challenger` with `challenger_allocation_pct` of initial capital, funded from reserve.

## The ledger
`data/events/evolution.jsonl` holds one `EvolutionRecord` per attempt: `proposal_id, kind,
strategy_id, outcome (applied|rejected|rolled_back|promoted|demoted|no_change|error), reason,
rationale, hypothesis, metrics, baseline_metrics, file_path, commit`. The daily report and the
next day's context both read it, so the researcher is told what already failed and why.

## Failure handling
* Researcher errors (API down, refusal, bad JSON) → `outcome=error`, loop ends, scheduler alive.
* Validator errors → `outcome=rejected [error]`.
* Apply errors → rollback of file and manifest, `outcome=rolled_back`.
* Nothing here can raise into the scheduler: `JobRunner` catches, records a `job_run` event and alerts.

## Tuning the bar
Too strict and nothing ever gets applied; too loose and noise gets promoted. Start with defaults,
watch `ta_evolution_total{outcome=...}` in Grafana and the `🧬/🚫` bullets in the daily report, then
adjust `required_improvement_pct`, `min_backtest_trades`, `max_backtest_drawdown_pct`.
