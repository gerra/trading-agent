"""The self-improvement loop. Read docs/EVOLUTION_LOOP.md first.

    loop.py        EvolutionLoop.run(): review -> research -> propose -> validate -> apply -> record
    context.py     what the researcher gets to see (performance, rejections, code, past attempts)
    proposal.py    the strictly-typed thing the researcher must return
    sandbox.py     static policy for generated code (import allow-list, forbidden builtins...)
    runner.py      subprocess that backtests a candidate under CPU/memory limits
    validator.py   static check + sandboxed backtest + comparison against the baseline
    applier.py     writes to the mutable zone, updates the manifest, git-commits, rolls back
    promotion.py   promotes/retires challengers based on *live* paper results
    performance.py shared helpers to compute per-strategy performance from the event store

This package must never import from `adapters`. It talks to the LLM only via ResearcherPort.
"""
