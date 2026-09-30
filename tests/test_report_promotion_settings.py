from datetime import timedelta
from pathlib import Path

from trading_agent.adapters.notifier.telegram_notifier import _chunks
from trading_agent.core.events import Event, snapshot_event
from trading_agent.core.models import PortfolioSnapshot, SleeveSnapshot
from trading_agent.settings import Settings, load_settings


def snap(ts, sleeves: dict[str, float]):
    return snapshot_event(
        PortfolioSnapshot(
            ts=ts,
            equity=sum(sleeves.values()),
            cash=0,
            positions_value=0,
            fees_paid_total=1.0,
            sleeves=[
                SleeveSnapshot(
                    sleeve_id=k,
                    strategy_id=k,
                    equity=v,
                    cash=v,
                    positions_value=0,
                    realized_pnl=0,
                    fees_paid=0,
                    open_positions=0,
                )
                for k, v in sleeves.items()
            ],
        )
    )


def test_report_has_at_most_max_bullets_and_leads_with_equity(container, clock):
    container.engine.tick()
    for _ in range(3):
        clock.advance(timedelta(minutes=5))
        container.engine.tick()
    container.store.append(
        Event(
            kind="evolution",
            ts=clock.now(),
            payload={
                "proposal_id": "p1",
                "kind": "new_strategy",
                "strategy_id": "x",
                "outcome": "applied",
                "reason": "beat baseline",
                "hypothesis": "fewer, better trades",
            },
        )
    )
    container.store.append(
        Event(
            kind="evolution",
            ts=clock.now(),
            payload={
                "proposal_id": "p2",
                "kind": "tune_params",
                "strategy_id": "y",
                "outcome": "rejected",
                "reason": "[gate] drawdown",
            },
        )
    )
    bullets = container.report.build(clock.now())
    assert 1 <= len(bullets) <= container.settings.report.max_bullets
    assert bullets[0].startswith("💰 Equity")
    assert any("🧬 applied" in b for b in bullets) and any(
        "🚫 1 evolution rejected" in b for b in bullets
    )
    text = container.report.format(bullets, clock.now())
    assert text.count("\n• ") == len(bullets)


def test_promotion_and_retirement_from_live_results(container, clock):
    reg, store = container.registry, container.store
    t0 = clock.now() - timedelta(days=10)
    for e in reg.manifest.entries:
        e.since = t0
    reg.save()
    for d in range(10):
        store.append(
            snap(
                t0 + timedelta(days=d),
                {
                    "ema_crossover": 500 + d,
                    "new_listing_momentum": 200 + 5 * d,
                    "mean_reversion": 150 - 3 * d,
                },
            )
        )
    from trading_agent.core.models import Fill, Side

    for i in range(12):  # challenger needs >= 5 live round trips to be promotable
        ts = clock.now() - timedelta(days=2) + timedelta(hours=i)
        store.append(
            Event(
                kind="fill",
                ts=ts,
                payload=Fill(
                    order_id=str(i),
                    sleeve_id="new_listing_momentum",
                    symbol="kraken:A/USD",
                    side=Side.BUY if i % 2 == 0 else Side.SELL,
                    qty=1,
                    price=100 + (i % 2),
                    fee_quote=0.1,
                    slippage_bps=1,
                    ts=ts,
                ).model_dump(mode="json"),
            )
        )
    records = container.evolution.reviewer.review(clock.now())
    outcomes = {r.strategy_id: r.outcome for r in records}
    assert outcomes["new_listing_momentum"] == "promoted"
    assert outcomes["mean_reversion"] == "demoted"
    assert reg.champion().id == "new_listing_momentum"
    assert reg.manifest.get("ema_crossover").status == "challenger"
    assert reg.manifest.get("mean_reversion").status == "retired"


def test_settings_yaml_then_env_precedence(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("risk:\n  initial_capital_quote: 2500\nreport:\n  hour: 21\n")
    monkeypatch.setenv("TA_REPORT__HOUR", "20")
    monkeypatch.setenv("TA_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    s = load_settings(cfg)
    assert (
        s.risk.initial_capital_quote == 2500
        and s.report.hour == 20
        and s.data_dir == (tmp_path / "d").resolve()
    )
    assert s.mode == "paper" and s.fee_schedule().exchange == "kraken"
    assert s.telegram_bot_token == "123:abc" and s.anthropic_api_key is None
    assert load_settings(tmp_path / "missing.yaml").risk.initial_capital_quote == 1000


def test_live_mode_is_impossible():
    import pytest

    with pytest.raises(ValueError):
        Settings(mode="live")


def test_telegram_chunking_keeps_lines_whole():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    parts = _chunks(text, 4000)
    assert all(len(p) <= 4000 for p in parts) and "".join(parts) == text
