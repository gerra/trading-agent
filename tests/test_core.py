from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.core import accounting
from trading_agent.core.fees import FeeSchedule, edge_clears_costs
from trading_agent.core.metrics import max_drawdown_pct, performance, round_trips
from trading_agent.core.models import Fill, Side, Sleeve

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FEES = FeeSchedule(exchange="x", maker_bps=10, taker_bps=40, slippage_bps=10, min_order_quote=5)


def fill(
    side: Side, qty: float, price: float, fee: float, minutes: int = 0, sleeve: str = "s"
) -> Fill:
    return Fill(
        order_id="o",
        sleeve_id=sleeve,
        symbol="x:A/USD",
        side=side,
        qty=qty,
        price=price,
        fee_quote=fee,
        slippage_bps=10,
        ts=T0 + timedelta(minutes=minutes),
    )


def test_round_trip_cost_counts_both_legs_and_slippage():
    assert FEES.round_trip_cost_bps() == 2 * (40 + 10)
    assert FEES.fee_for(1000.0) == pytest.approx(4.0)


def test_edge_must_clear_multiple_of_cost():
    assert not edge_clears_costs(None, FEES, 2.0)
    assert not edge_clears_costs(150.0, FEES, 2.0)
    assert edge_clears_costs(200.0, FEES, 2.0)


def test_apply_fill_buy_then_sell_tracks_cash_pnl_and_fees():
    s = Sleeve(id="s", strategy_id="s", cash=1000.0, initial_cash=1000.0)
    s = accounting.apply_fill(s, fill(Side.BUY, qty=2, price=100, fee=0.8))
    assert s.cash == pytest.approx(1000 - 200 - 0.8)
    assert s.position("x:A/USD").qty == 2 and s.position("x:A/USD").avg_price == 100
    s = accounting.apply_fill(s, fill(Side.SELL, qty=2, price=110, fee=0.88, minutes=5))
    assert s.cash == pytest.approx(1000 - 200 - 0.8 + 220 - 0.88)
    assert s.realized_pnl == pytest.approx(20 - 0.88)
    assert s.fees_paid == pytest.approx(1.68)
    assert not s.position("x:A/USD").is_open


def test_apply_fill_rejects_overspend_and_overselling():
    s = Sleeve(id="s", strategy_id="s", cash=10.0, initial_cash=10.0)
    with pytest.raises(ValueError, match="insufficient cash"):
        accounting.apply_fill(s, fill(Side.BUY, qty=1, price=100, fee=0.4))
    with pytest.raises(ValueError, match="cannot sell"):
        accounting.apply_fill(s, fill(Side.SELL, qty=1, price=100, fee=0.4))


def test_snapshot_sums_sleeves_at_market():
    a = accounting.apply_fill(
        Sleeve(id="a", strategy_id="a", cash=500, initial_cash=500),
        fill(Side.BUY, 1, 100, 0.4, sleeve="a"),
    )
    b = Sleeve(id="b", strategy_id="b", cash=300, initial_cash=300)
    snap = accounting.snapshot(T0, [a, b], {"x:A/USD": 120.0})
    assert snap.positions_value == pytest.approx(120)
    assert snap.equity == pytest.approx(500 - 100.4 + 120 + 300)
    assert snap.fees_paid_total == pytest.approx(0.4)


def test_round_trips_fifo_and_performance():
    fills = [
        fill(Side.BUY, 1, 100, 0.4),
        fill(Side.BUY, 1, 110, 0.44, 1),
        fill(Side.SELL, 2, 120, 0.96, 2),
    ]
    trips = round_trips(fills)
    assert [t.entry_price for t in trips] == [100, 110]
    assert sum(t.pnl for t in trips) == pytest.approx(20 + 10 - 0.4 - 0.44 - 0.96)
    rep = performance([1000, 1010, 990, 1030], fills, periods_per_year=100)
    assert rep.trades == 2 and rep.win_rate == 1.0
    assert rep.max_drawdown_pct == pytest.approx((1010 - 990) / 1010 * 100)
    assert max_drawdown_pct([100, 50, 100]) == 50
