"""Shared fixtures. Everything runs offline against synthetic data in a temp directory."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.adapters.clock import FixedClock
from trading_agent.adapters.market_data.synthetic_market_data import SyntheticMarketData
from trading_agent.adapters.notifier.telegram_notifier import NullNotifier
from trading_agent.container import Container, build_container
from trading_agent.settings import Settings

START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        data_dir=tmp_path / "data",
        generated_strategies_dir=tmp_path / "generated",
        exchange={"id": "kraken", "discovery_exchanges": ["kraken"]},
        universe={
            "core_symbols": ["kraken:BTC/USD", "kraken:ETH/USD"],
            "candle_history": 320,
            "timeframe": "5m",
        },
        risk={"initial_capital_quote": 1000.0, "min_edge_to_cost_multiple": 1.0},
        evolution={
            "git_commit": False,
            "min_backtest_bars": 100,
            "min_backtest_trades": 1,
            "subprocess_timeout_s": 90,
        },
    )
    s.ensure_dirs()
    (s.generated_strategies_dir / "__init__.py").write_text("")
    return s


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest.fixture
def container(settings: Settings, clock: FixedClock) -> Container:
    md = SyntheticMarketData("kraken", seed=3, symbols=settings.universe.core_symbols, clock=clock)
    return build_container(settings, market_data=md, clock=clock, notifier=NullNotifier())
