from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.adapters.clock import FixedClock
from trading_agent.adapters.store.jsonl_event_store import JsonlEventStore
from trading_agent.discovery.new_listings import NewListingTracker
from trading_agent.evolution.sandbox import check_source
from trading_agent.strategies.registry import StrategyEntry, StrategyRegistry

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeMarkets:
    exchange_id = "kraken"

    def __init__(self, symbols):
        self.symbols = symbols

    def list_symbols(self):
        return list(self.symbols)

    def get_candles(self, *a):
        return []

    def get_ticker(self, *a):
        raise NotImplementedError


def test_registry_creates_default_manifest_and_instantiates(tmp_path):
    reg = StrategyRegistry(tmp_path / "m.json", tmp_path / "gen", check_source)
    ids = {e.id for e in reg.active_entries()}
    assert ids == {"trend_breakout", "tsmom", "new_listing_momentum", "mean_reversion"}
    assert reg.champion().id == "trend_breakout"
    strategies = reg.instantiate_active()
    assert set(strategies) == ids
    reg.set_params("trend_breakout", {"channel_bars": 96})
    reg2 = StrategyRegistry(tmp_path / "m.json", tmp_path / "gen", check_source)
    assert reg2.manifest.get("trend_breakout").params == {"channel_bars": 96}
    assert reg2.manifest.get("trend_breakout").version == 2


def test_registry_refuses_bad_generated_code_and_escapes(tmp_path):
    gen = tmp_path / "gen"
    gen.mkdir()
    (gen / "evil.py").write_text(
        "import os\nfrom trading_agent.strategies.base import Strategy\nclass Evil(Strategy):\n    id='evil'\n"
    )
    reg = StrategyRegistry(tmp_path / "m.json", gen, check_source)
    entry = StrategyEntry(
        id="evil",
        origin="generated",
        module="evil.py",
        class_name="Evil",
        status="challenger",
        allocation_pct=5,
        since=T0,
    )
    with pytest.raises(PermissionError, match="policy"):
        reg.instantiate(entry)
    outside = StrategyEntry(
        id="x",
        origin="generated",
        module="../m.json",
        class_name="X",
        status="challenger",
        allocation_pct=5,
        since=T0,
    )
    with pytest.raises(PermissionError, match="mutable zone"):
        reg.instantiate(outside)
    reg.add_generated(entry)
    assert reg.instantiate_active().keys() == {
        "trend_breakout",
        "tsmom",
        "new_listing_momentum",
        "mean_reversion",
    }  # evil skipped, logged


def test_discovery_baseline_then_detects_new_listing(tmp_path):
    clock = FixedClock(T0)
    store = JsonlEventStore(tmp_path / "events")
    markets = FakeMarkets(["kraken:BTC/USD", "kraken:ETH/USD"])
    tracker = NewListingTracker({"kraken": markets}, tmp_path / "listings.json", store, clock)
    assert tracker.scan() == []  # first scan = baseline only
    markets.symbols.append("kraken:NEW/USD")
    clock.advance(timedelta(hours=1))
    found = tracker.scan()
    assert [n.symbol for n in found] == ["kraken:NEW/USD"]
    assert found[0].base == "NEW" and found[0].first_seen == clock.now()
    assert len(list(store.read("new_listing"))) == 1
    assert tracker.scan() == []  # not reported twice
    tracker2 = NewListingTracker({"kraken": markets}, tmp_path / "listings.json", store, clock)
    assert [n.symbol for n in tracker2.recent(clock.now(), timedelta(days=1))] == ["kraken:NEW/USD"]
    assert tracker2.recent(clock.now() + timedelta(days=3), timedelta(days=1)) == []
