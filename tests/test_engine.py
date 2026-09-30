from datetime import timedelta

from trading_agent.core.models import Position, Side, Sleeve
from trading_agent.engine.state import DayStats


def test_tick_creates_sleeves_snapshots_and_persists_state(container):
    res = container.engine.tick()
    st = container.engine.state
    assert set(st.sleeves) == {"ema_crossover", "new_listing_momentum", "mean_reversion"}
    assert st.reserve_cash + sum(s.cash for s in st.sleeves.values()) == 1000.0
    assert res.snapshot.equity == 1000.0
    assert len(list(container.store.read("snapshot"))) == 1
    assert container.state_repo.load().ticks == 1
    assert container.candle_store.read("kraken:BTC/USD", "5m")


def test_tick_is_robust_to_strategy_and_market_errors(container, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("exchange down")

    monkeypatch.setattr(container.market_data, "get_candles", boom)
    res = container.engine.tick()
    assert res.errors and res.fills == 0
    assert res.snapshot.equity == 1000.0


def test_forced_exit_on_stale_position_and_liquidation(container, clock):
    container.engine.tick()
    st = container.engine.state
    sleeve = st.sleeves["ema_crossover"]
    sleeve.positions["kraken:BTC/USD"] = Position(
        symbol="kraken:BTC/USD",
        qty=0.01,
        avg_price=100.0,
        opened_at=clock.now() - timedelta(days=5),
    )
    sleeve.cash -= 1.0
    st.sleeves["ema_crossover"] = sleeve
    clock.advance(timedelta(minutes=5))
    res = container.engine.tick()
    fills = list(container.store.read("fill"))
    assert res.fills >= 1 and fills[-1].payload["side"] == "sell"
    assert not container.engine.state.sleeves["ema_crossover"].position("kraken:BTC/USD").is_open
    signal_reasons = [e.payload["reason"] for e in container.store.read("signal")]
    assert "engine: max position age" in signal_reasons


def test_retired_strategy_sleeve_is_liquidated_and_cash_returned(container, clock):
    container.engine.tick()
    before = container.engine.state.reserve_cash
    container.registry.set_status("mean_reversion", "retired", allocation_pct=0)
    clock.advance(timedelta(minutes=5))
    container.engine.tick()
    st = container.engine.state
    assert "mean_reversion" not in st.sleeves and "mean_reversion" not in st.liquidating
    assert st.reserve_cash > before


def test_daily_loss_breaker_blocks_entries(container, clock):
    container.engine.tick()
    st = container.engine.state
    st.day = DayStats(
        date=st.day.date, start_equity=2000.0
    )  # pretend we started the day much richer
    clock.advance(timedelta(minutes=5))
    container.engine.tick()
    assert container.engine.state.day.daily_loss_tripped
    trips = list(container.store.read("circuit_breaker"))
    assert trips and trips[-1].payload["name"] == "daily_loss"


def test_drawdown_breaker_halts_until_resume(container, clock):
    container.engine.tick()
    container.engine.state.peak_equity = 5000.0
    clock.advance(timedelta(minutes=5))
    container.engine.tick()
    assert container.engine.state.halted_reason
    assert not container.health()["ok"]
    container.engine.resume()
    assert container.engine.state.halted_reason is None


def test_engine_refuses_mode_mismatch(settings, clock):
    import pytest

    from trading_agent.container import build_container

    class LiveBroker:
        mode = "live"

        def execute(self, *a):
            raise AssertionError

    with pytest.raises(RuntimeError, match="refusing to start"):
        build_container(settings, synthetic=True, clock=clock, broker=LiveBroker())


def test_sleeve_model_rejects_negative_cash():
    import pytest

    with pytest.raises(ValueError):
        Sleeve(id="s", strategy_id="s", cash=-1, initial_cash=1)
    assert Side.BUY.value == "buy"
