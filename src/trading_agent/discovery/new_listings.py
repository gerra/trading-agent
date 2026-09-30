"""NewListingTracker: diff each exchange's market list against the last scan.

The first scan of an exchange only records a baseline (otherwise every coin would look new).
Listings are persisted in data/state/new_listings.json and emitted as `new_listing` events.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from trading_agent.core.events import new_listing_event
from trading_agent.core.models import NewListing
from trading_agent.ports import ClockPort, EventStorePort, MarketDataPort

log = structlog.get_logger(__name__)


class ExchangeScan(BaseModel):
    known: list[str] = Field(default_factory=list)
    baseline_at: datetime | None = None
    last_scan: datetime | None = None


class ListingState(BaseModel):
    exchanges: dict[str, ExchangeScan] = Field(default_factory=dict)
    listings: list[NewListing] = Field(default_factory=list)


class NewListingTracker:
    def __init__(
        self,
        sources: dict[str, MarketDataPort],
        state_path: Path,
        store: EventStorePort,
        clock: ClockPort,
    ) -> None:
        self.sources = sources
        self.state_path = state_path
        self.store = store
        self.clock = clock
        self.state = self._load()

    def _load(self) -> ListingState:
        if self.state_path.exists():
            return ListingState.model_validate_json(self.state_path.read_text())
        return ListingState()

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json(indent=2))
        tmp.replace(self.state_path)

    def scan(self) -> list[NewListing]:
        now = self.clock.now()
        found: list[NewListing] = []
        for ex_id, source in self.sources.items():
            try:
                symbols = set(source.list_symbols())
            except Exception as exc:  # noqa: BLE001
                log.warning("discovery_scan_failed", exchange=ex_id, error=str(exc))
                continue
            scan = self.state.exchanges.setdefault(ex_id, ExchangeScan())
            if scan.baseline_at is None:
                scan.known, scan.baseline_at, scan.last_scan = sorted(symbols), now, now
                log.info("discovery_baseline", exchange=ex_id, markets=len(symbols))
                continue
            new = symbols - set(scan.known)
            for instrument in sorted(new):
                _, pair = instrument.split(":", 1)
                base, quote = pair.split("/", 1)
                listing = NewListing(
                    exchange=ex_id, symbol=instrument, base=base, quote=quote, first_seen=now
                )
                found.append(listing)
                self.state.listings.append(listing)
                self.store.append(new_listing_event(now, listing))
                log.info("new_listing", exchange=ex_id, symbol=instrument)
            scan.known, scan.last_scan = sorted(symbols), now
        self._prune(now)
        self._save()
        return found

    def recent(self, now: datetime, max_age: timedelta) -> list[NewListing]:
        return sorted(
            (n for n in self.state.listings if now - n.first_seen <= max_age),
            key=lambda n: n.first_seen,
            reverse=True,
        )

    def _prune(self, now: datetime, keep: timedelta = timedelta(days=90)) -> None:
        self.state.listings = [n for n in self.state.listings if now - n.first_seen <= keep]
