# ADR 0004 — One mutable zone, three-layer sandbox, backtest gate

**Status**: accepted · **Date**: 2026-09-30

## Context
The agent must be able to change its own strategy code, but must not be able to harm the host,
the repo, or the portfolio.

## Decision
* The loop may write only `.py` files into `strategies/generated/` (path-confined) and edit the
  manifest through `ProposalApplier`.
* Generated code passes an AST allow-list (`sandbox.py`) before every import, is backtested in a
  resource-limited subprocess, and must beat the baseline on the same data.
* Strategies are pure: they receive a `StrategyContext` and return `Signal`s; the engine owns
  money, IO and risk.
* Promotion to champion depends on live paper results, not on backtests.

## Consequences
Self-improvement is bounded, auditable (`git log`, `evolution.jsonl`) and reversible. The cost is
that some legitimate ideas (e.g. needing numpy) are out of scope until the allow-list is widened
by a human.
