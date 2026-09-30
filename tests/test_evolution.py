"""End-to-end tests of the self-improvement loop with a fake researcher (no LLM, no network)."""

from __future__ import annotations

from datetime import timedelta

from trading_agent.adapters.market_data.synthetic_market_data import generate_candles
from trading_agent.adapters.researcher.static_researcher import StaticResearcher
from trading_agent.container import build_container
from trading_agent.evolution.proposal import ProposalDraft
from trading_agent.evolution.sandbox import check_source

NEW_STRATEGY = """
from typing import Any, ClassVar

from trading_agent.core.models import Signal
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.indicators import sma


class Breakout(Strategy):
    id: ClassVar[str] = "breakout_v1"
    description: ClassVar[str] = "Buy above 30-bar mean, sell below."
    default_params: ClassVar[dict[str, Any]] = {"n": 30}

    def required_history(self) -> int:
        return 40

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out: list[Signal] = []
        for sym, cs in ctx.candles.items():
            closes = [c.close for c in cs]
            m = sma(closes, int(self.params["n"]))[-1]
            if m is None:
                continue
            if closes[-1] > m * 1.002 and not ctx.has_position(sym):
                out.append(self.buy(sym, 0.6, "breakout", 400.0))
            elif closes[-1] < m and ctx.has_position(sym):
                out.append(self.sell(sym, "back below mean"))
        return out
"""


def draft(
    kind="new_strategy",
    strategy_id="breakout_v1",
    source=NEW_STRATEGY,
    class_name="Breakout",
    params_json="{}",
) -> ProposalDraft:
    return ProposalDraft(
        kind=kind,
        strategy_id=strategy_id,
        class_name=class_name,
        source_code=source,
        params_json=params_json,
        rationale="test",
        hypothesis="beats champion",
        expected_improvement="+1pp",
        risks="none",
    )


def seed_candles(container, n=700):
    for sym in container.settings.universe.core_symbols:
        container.candle_store.append(sym, "5m", generate_candles(sym, n, "5m", seed=5))


def build(settings, clock, d: ProposalDraft):
    c = build_container(settings, synthetic=True, clock=clock, researcher=StaticResearcher(d))
    seed_candles(c)
    return c


def test_valid_new_strategy_is_applied_or_rejected_by_gate_only(settings, clock):
    settings.evolution.require_positive_return = False
    settings.evolution.required_improvement_pct = (
        -100.0
    )  # anything that runs cleanly should pass the gate
    c = build(settings, clock, draft())
    records = c.evolution.run()
    rec = records[-1]
    assert rec.outcome == "applied", rec.reason
    assert (settings.generated_strategies_dir / "breakout_v1.py").exists()
    entry = c.registry.manifest.get("breakout_v1")
    assert entry and entry.status == "challenger" and entry.origin == "generated"
    assert "trades" in rec.metrics and "total_return_pct" in rec.baseline_metrics
    assert check_source((settings.generated_strategies_dir / "breakout_v1.py").read_text()) == []
    # the engine picks it up on the next tick and funds a sleeve for it
    c.engine.tick()
    assert "breakout_v1" in c.engine.state.sleeves
    # daily budget: a second run today does not call the researcher again
    assert c.evolution.run() == []


def test_malicious_code_is_rejected_at_policy_stage_and_never_written(settings, clock):
    evil = NEW_STRATEGY.replace(
        "from typing import Any, ClassVar", "import os\nfrom typing import Any, ClassVar"
    )
    c = build(settings, clock, draft(strategy_id="evil_one", source=evil))
    rec = c.evolution.run()[-1]
    assert rec.outcome == "rejected" and rec.reason.startswith("[policy]") and "os" in rec.reason
    assert not (settings.generated_strategies_dir / "evil_one.py").exists()
    assert c.registry.manifest.get("evil_one") is None


def test_runtime_crash_in_candidate_is_contained(settings, clock):
    crashy = NEW_STRATEGY.replace('m = sma(closes, int(self.params["n"]))[-1]', "m = 1 / 0")
    c = build(settings, clock, draft(strategy_id="crashy", source=crashy))
    rec = c.evolution.run()[-1]
    assert (
        rec.outcome == "rejected"
        and "[backtest]" in rec.reason
        and "ZeroDivisionError" in rec.reason
    )


def test_gate_rejects_when_not_beating_baseline(settings, clock):
    settings.evolution.required_improvement_pct = 1000.0
    c = build(settings, clock, draft(strategy_id="never_good"))
    rec = c.evolution.run()[-1]
    assert rec.outcome == "rejected" and "[gate]" in rec.reason
    assert not (settings.generated_strategies_dir / "never_good.py").exists()


def test_tune_params_and_schema_errors(settings, clock):
    settings.evolution.require_positive_return = False
    settings.evolution.required_improvement_pct = -100.0
    c = build(
        settings,
        clock,
        draft(
            kind="tune_params",
            strategy_id="ema_crossover",
            source="",
            class_name="",
            params_json='{"fast": 8, "slow": 30}',
        ),
    )
    rec = c.evolution.run()[-1]
    assert rec.outcome == "applied", rec.reason
    assert c.registry.manifest.get("ema_crossover").params == {"fast": 8, "slow": 30}

    clock.advance(timedelta(days=1, minutes=1))
    c2 = build_container(
        settings,
        synthetic=True,
        clock=clock,
        researcher=StaticResearcher(
            draft(kind="tune_params", strategy_id="nope", params_json="{}")
        ),
    )
    rec = c2.evolution.run()[-1]
    assert rec.outcome == "rejected" and "[schema]" in rec.reason


def test_insufficient_data_is_reported_not_crashed(settings, clock):
    c = build_container(settings, synthetic=True, clock=clock, researcher=StaticResearcher(draft()))
    rec = c.evolution.run()[-1]
    assert rec.outcome == "rejected" and "[data]" in rec.reason


def test_retire_requires_evidence(settings, clock):
    c = build(
        settings,
        clock,
        draft(kind="retire_strategy", strategy_id="ema_crossover", source="", class_name=""),
    )
    rec = c.evolution.run()[-1]
    assert rec.outcome == "rejected" and "champion" in rec.reason


def test_offline_default_is_no_change(settings, clock):
    c = build_container(settings, synthetic=True, clock=clock)
    rec = c.evolution.run()[-1]
    assert rec.outcome == "no_change"
    assert list(settings.research_dir.glob("*.md"))
