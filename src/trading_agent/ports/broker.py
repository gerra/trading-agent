from __future__ import annotations

from typing import Protocol

from trading_agent.core.models import Fill, Order


class BrokerPort(Protocol):
    """Turns an Order into a Fill. Paper broker simulates; a live broker would call an exchange.

    The broker does NOT own money. Sleeve accounting lives in `core.accounting` and is applied by
    the engine, so any broker implementation is trivially auditable.
    """

    @property
    def mode(self) -> str:
        """'paper' or 'live'. The engine refuses to run unless this matches settings.mode."""
        ...

    def execute(self, order: Order, reference_price: float) -> Fill: ...
