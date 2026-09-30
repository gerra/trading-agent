"""Append-only JSONL event store: one file per event kind under data/events/.

Plain files on purpose: `cat`, `jq`, pandas and future training jobs all read them directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from trading_agent.core.events import Event, EventKind


class JsonlEventStore:
    def __init__(self, events_dir: Path) -> None:
        self.dir = events_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str) -> Path:
        return self.dir / f"{kind}.jsonl"

    def append(self, event: Event) -> None:
        with self._path(event.kind).open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def read(
        self, kind: EventKind, since: datetime | None = None, until: datetime | None = None
    ) -> Iterable[Event]:
        path = self._path(kind)
        if not path.exists():
            return []
        out: list[Event] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = Event.model_validate_json(line)
                except ValueError:
                    continue  # a torn write from a crash must not poison every read
                if since and ev.ts < since:
                    continue
                if until and ev.ts > until:
                    continue
                out.append(ev)
        out.sort(key=lambda e: e.ts)
        return out
