# ADR 0001 — Python, ccxt, paper-first

**Status**: accepted · **Date**: 2026-09-30

## Context
We need broad exchange coverage from the UK, public market data without accounts, an LLM SDK,
and a language that both humans and AI agents write fluently.

## Decision
Python 3.11+, `ccxt` for public market data (no keys needed for paper trading), Kraken as the
default fee model (UK-available, FCA-registered), other exchanges' public market lists for
new-listing discovery. Live trading is explicitly not implemented (`mode: Literal["paper"]`).

## Consequences
Anyone can run it with zero accounts. Going live is a deliberate, separate project (new ADR,
new broker adapter, human approval flow).
