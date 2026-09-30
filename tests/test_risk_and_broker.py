from datetime import UTC, datetime

import pytest

from trading_agent.adapters.broker.paper_broker import PaperBroker
from trading_agent.adapters.clock import FixedClock
from trading_agent.core.fees import DEFAULT_FEE_SCHEDULES
from trading_agent.core.models import Order, Position, Side, Signal, Sleeve
from trading_agent.engine.risk import RiskManager
from trading_agent.engine.state import DayStats, EngineState
from trading_agent.settings import RiskSettings

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SYM = "kraken:BTC/USD"


def state(**kw) -> EngineState:
    return EngineState(started_at=T0, day=DayStats(date="2026-01-01", start_equity=1000), **kw)


def buy(edge: float | None = 500.0, strength: float = 1.0) -> Signal:
    return Signal(
        strategy_id="s",
        symbol=SYM,
        side=Side.BUY,
        strength=strength,
        reason="t",
        expected_edge_bps=edge,
    )


@pytest.fixture
def risk() -> RiskManager:
    return RiskManager(
        RiskSettings(initial_capital_quote=1000, min_edge_to_cost_multiple=2.0),
        DEFAULT_FEE_SCHEDULES,
    )


def test_paper_broker_applies_adverse_slippage_and_taker_fee():
    broker = PaperBroker(DEFAULT_FEE_SCHEDULES, FixedClock(T0))
    f = broker.execute(Order(id="1", sleeve_id="s", symbol=SYM, side=Side.BUY, qty=1, ts=T0), 100.0)
    assert f.price == pytest.approx(100 * 1.001)
    assert f.fee_quote == pytest.approx(100.1 * 0.004)
    g = broker.execute(
        Order(id="2", sleeve_id="s", symbol=SYM, side=Side.SELL, qty=1, ts=T0), 100.0
    )
    assert g.price == pytest.approx(99.9)


def test_sell_requires_position(risk):
    s = Sleeve(id="s", strategy_id="s", cash=100, initial_cash=100)
    assert (
        risk.evaluate(
            Signal(strategy_id="s", symbol=SYM, side=Side.SELL, strength=1, reason="x"),
            s,
            state(),
            100,
        ).reason
        == "no_position"
    )
    s.positions[SYM] = Position(symbol=SYM, qty=0.5, avg_price=90)
    d = risk.evaluate(
        Signal(strategy_id="s", symbol=SYM, side=Side.SELL, strength=1, reason="x"), s, state(), 100
    )
    assert d.approved and d.qty == 0.5


def test_buy_rejections_in_priority_order(risk):
    s = Sleeve(id="s", strategy_id="s", cash=1000, initial_cash=1000)
    assert risk.evaluate(buy(), s, state(halted_reason="dd"), 100).reason.startswith("halted")
    assert risk.evaluate(buy(), s, state(liquidating=["s"]), 100).reason == "sleeve_liquidating"
    st = state()
    st.day.daily_loss_tripped = True
    assert risk.evaluate(buy(), s, st, 100).reason == "daily_loss_limit"
    st = state()
    st.day.trades_by_sleeve["s"] = 99
    assert risk.evaluate(buy(), s, st, 100).reason == "trade_rate_limit"
    assert (
        risk.evaluate(buy(edge=150), s, state(), 100).reason == "edge_below_cost"
    )  # kraken rt cost = 100bps, x2
    assert risk.evaluate(buy(edge=None), s, state(), 100).reason == "edge_below_cost"


def test_buy_sizing_and_min_order(risk):
    s = Sleeve(id="s", strategy_id="s", cash=1000, initial_cash=1000)
    d = risk.evaluate(buy(strength=0.5), s, state(), 100)
    assert (
        d.approved
        and d.notional_quote == pytest.approx(1000 * 0.25 * 0.5)
        and d.qty == pytest.approx(1.25)
    )
    tiny = Sleeve(id="s", strategy_id="s", cash=10, initial_cash=10)
    assert risk.evaluate(buy(), tiny, state(), 100).reason == "below_min_order"
    s.positions[SYM] = Position(symbol=SYM, qty=1, avg_price=100)
    assert risk.evaluate(buy(), s, state(), 100).reason == "already_in_position"
