# ADR 0003 — Append-only JSONL events as the source of truth

**Status**: accepted · **Date**: 2026-09-30

## Decision
Everything the agent does is an `Event` appended to `data/events/<kind>.jsonl`. Reports, the
research context, promotion decisions and future training all read those files. State files
(`engine_state.json`, manifest) are derived conveniences, rewritten atomically.

## Consequences
Trivially inspectable (`jq`, pandas), crash-tolerant (torn lines are skipped), no database to
operate. Reads are linear scans; fine at this volume, and easy to move to SQLite/Parquet later
behind `EventStorePort`.
