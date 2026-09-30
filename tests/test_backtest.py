from trading_agent.adapters.market_data.synthetic_market_data import generate_candles
from trading_agent.backtest.simulator import run_backtest
from trading_agent.core.fees import DEFAULT_FEE_SCHEDULES
from trading_agent.settings import RiskSettings
from trading_agent.strategies.builtin.mean_reversion import MeanReversion
from trading_agent.strategies.builtin.trend_breakout import TrendBreakout


def test_backtest_runs_and_reports_metrics():
    candles = {
        s: generate_candles(s, 800, "5m", seed=11) for s in ("binance:BTC/USDT", "binance:ETH/USDT")
    }
    risk = RiskSettings(initial_capital_quote=1000, min_edge_to_cost_multiple=1.0)
    res = run_backtest(
        TrendBreakout({"channel_bars": 48, "exit_bars": 12, "min_atr_bps": 0}),
        candles,
        risk,
        DEFAULT_FEE_SCHEDULES,
        1000.0,
        "5m",
    )
    assert res.bars == 800 and len(res.equity_curve) == 800
    assert res.signals > 0
    m = res.metrics()
    assert set(m) >= {
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe",
        "trades",
        "fees_paid",
        "bars",
    }
    assert m["fees_paid"] >= 0
    # every fill happened at a bar open (no look-ahead): price must equal some open +/- slippage
    opens = {(c.symbol, c.ts): c.open for cs in candles.values() for c in cs}
    for f in res.fills:
        ref = opens[(f.symbol, f.ts)]
        assert abs(f.price / ref - 1) < 0.002


def test_backtest_is_deterministic():
    candles = {"binance:BTC/USDT": generate_candles("binance:BTC/USDT", 600, "5m", seed=2)}
    risk = RiskSettings(initial_capital_quote=500, min_edge_to_cost_multiple=1.0)
    a = run_backtest(MeanReversion(), candles, risk, DEFAULT_FEE_SCHEDULES, 500.0, "5m")
    b = run_backtest(MeanReversion(), candles, risk, DEFAULT_FEE_SCHEDULES, 500.0, "5m")
    assert a.equity_curve == b.equity_curve and a.metrics() == b.metrics()
