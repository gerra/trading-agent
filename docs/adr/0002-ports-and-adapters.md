# ADR 0002 — Ports and adapters, single composition root

**Status**: accepted · **Date**: 2026-09-30

## Decision
Services depend on `Protocol`s in `ports/`; concrete implementations live in `adapters/`; only
`container.py` wires them. Tests inject fakes through `build_container(**overrides)`.

## Consequences
Swapping the exchange, notifier, LLM or storage is one adapter + one line in the container.
Every service is testable offline. Agents can find "what talks to what" in a single file.
