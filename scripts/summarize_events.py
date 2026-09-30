"""Offline analysis of the event store: per-strategy performance over a window.

Usage: python scripts/summarize_events.py [--days 7] [--data-dir data]
Reads only data/events/*.jsonl; safe to run while the agent is running.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_agent.adapters.store.jsonl_event_store import JsonlEventStore
from trading_agent.evolution.performance import portfolio_equity_series, strategy_performance


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()
    store = JsonlEventStore(args.data_dir / "events")
    since = datetime.now(UTC) - timedelta(days=args.days)
    series = portfolio_equity_series(store, since=since)
    ids = sorted(
        {
            s["strategy_id"]
            for ev in store.read("snapshot", since=since)
            for s in ev.payload["sleeves"]
        }
    )
    out = {
        "window_days": args.days,
        "portfolio": {"start": series[0][1], "end": series[-1][1]} if series else None,
        "strategies": {
            sid: strategy_performance(store, sid, since=since).model_dump() for sid in ids
        },
        "evolution": [ev.payload for ev in store.read("evolution", since=since)],
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
