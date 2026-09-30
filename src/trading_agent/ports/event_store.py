from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from trading_agent.core.events import Event, EventKind


class EventStorePort(Protocol):
    """Append-only log of everything the agent did. See core/events.py for the schemas."""

    def append(self, event: Event) -> None: ...

    def read(
        self, kind: EventKind, since: datetime | None = None, until: datetime | None = None
    ) -> Iterable[Event]:
        """Events of one kind in chronological order, optionally bounded by time."""
        ...
