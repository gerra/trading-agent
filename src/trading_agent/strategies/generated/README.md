# generated/ — the mutable zone

This directory is the **only** place the evolution loop (`src/trading_agent/evolution/`) is allowed
to write code. Every file here:

* was proposed by the researcher, passed `evolution/sandbox.py` static policy, passed the
  backtest gate in `evolution/validator.py`, and was recorded in `data/events/evolution.jsonl`
  with its rationale and metrics;
* defines exactly one `Strategy` subclass and imports only from the allow-list in `sandbox.py`;
* is listed in `data/state/strategy_manifest.json` with a status (`challenger`, `champion`, `retired`).

Humans may edit or delete files here freely. If you delete one, also remove (or retire) its
manifest entry, otherwise the registry will log a load error and skip it.
