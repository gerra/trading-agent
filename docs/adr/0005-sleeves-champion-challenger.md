# ADR 0005 — Per-strategy sleeves with champion/challenger

**Status**: accepted · **Date**: 2026-09-30

## Decision
Capital is split into sleeves, one per strategy, funded from a reserve according to
`allocation_pct` at creation. The champion is a label earned by live outperformance; challengers
start small. Retiring a strategy liquidates its sleeve back to the reserve.

## Consequences
Strategies are compared on equal footing and isolated from each other's losses. Portfolio equity is
the sum of sleeves + reserve. Rebalancing between sleeves is intentionally manual for now.
