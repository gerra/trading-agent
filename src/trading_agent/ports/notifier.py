from __future__ import annotations

from typing import Protocol


class NotifierPort(Protocol):
    def send(self, text: str) -> None:
        """Deliver a short plain-text message to the human. Must never raise into the caller."""
        ...
