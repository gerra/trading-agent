"""Strategies: pure signal generators.

Contract (see base.py): a strategy receives a `StrategyContext` (candles, its own positions,
the round-trip cost in bps, recent new listings, its params) and returns `Signal`s. It never
touches orders, money, network or files. That is what makes generated strategies safe to run.

    builtin/    hand-written, reviewed strategies (immutable for the evolution loop)
    generated/  the MUTABLE ZONE: the only place the evolution loop may write code
    registry.py which strategies are active, with what status and allocation (manifest)
"""
