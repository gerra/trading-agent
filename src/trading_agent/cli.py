"""Command line entrypoint: `trading-agent <command>`.

run        start the scheduler (tick, discovery, evolve, report) + /metrics + /healthz
tick       run exactly one trading tick and exit
discover   scan exchanges for new listings once
evolve     run one self-improvement iteration (--dry-run: validate but do not apply)
report     build the daily summary (--send: deliver via Telegram)
backtest   backtest a strategy from the manifest on stored candles
backfill   download candle history for the core universe into the local cache
status     print engine state and active strategies
resume     clear a drawdown halt after a human has reviewed it
check      validate every generated strategy against the sandbox policy
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

import structlog

from trading_agent.container import Container, build_container
from trading_agent.observability.logging import configure_logging
from trading_agent.settings import Settings, load_settings

log = structlog.get_logger(__name__)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trading-agent",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="path to settings.yaml (default: config/settings.yaml)",
    )
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="use deterministic synthetic market data (no network)",
    )
    p.add_argument("--pretty-logs", action="store_true", help="human-readable logs instead of JSON")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("tick")
    sub.add_parser("discover")
    e = sub.add_parser("evolve")
    e.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("--send", action="store_true")
    b = sub.add_parser("backtest")
    b.add_argument("--strategy", required=True)
    b.add_argument("--bars", type=int, default=2000)
    bf = sub.add_parser("backfill")
    bf.add_argument("--bars", type=int, default=1000)
    sub.add_parser("status")
    sub.add_parser("resume")
    sub.add_parser("check")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings(args.config)
    settings.ensure_dirs()
    configure_logging(
        settings.observability.log_level,
        json_logs=not args.pretty_logs,
        log_file=settings.logs_dir / "agent.jsonl",
    )
    c = build_container(settings, synthetic=args.synthetic)
    return COMMANDS[args.command](c, args)


def cmd_run(c: Container, args: argparse.Namespace) -> int:
    from trading_agent.observability.health import start_health_server
    from trading_agent.scheduler.jobs import build_scheduler

    s = c.settings
    start_health_server(s.observability.metrics_port, c.metrics, c.health)
    log.info(
        "starting",
        mode=s.mode,
        exchange=s.exchange.id,
        metrics_port=s.observability.metrics_port,
        evolution=s.evolution.enabled,
    )
    if s.schedule.backfill_on_start and not args.synthetic:
        try:
            _backfill(c, s.universe.candle_history * 3)
        except Exception as exc:  # noqa: BLE001
            log.warning("backfill_failed", error=str(exc))
    c.job_runner.wrap("discovery", c.listings.scan)()
    c.job_runner.wrap("tick", c.engine.tick)()
    sched = build_scheduler(
        c.job_runner,
        c.engine.tick,
        c.listings.scan,
        c.evolution.run,
        lambda: _send_report(c),
        s.schedule.tick_minutes,
        s.schedule.discovery_minutes,
        s.schedule.evolution_hour_utc,
        s.report.hour,
        s.report.minute,
        s.report.timezone,
        s.evolution.enabled,
    )
    c.notifier.send(
        f"🟢 trading-agent started (paper, {s.exchange.id}, {len(c.registry.active_entries())} strategies)"
    )
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("stopping")
    return 0


def cmd_tick(c: Container, args: argparse.Namespace) -> int:
    res = c.engine.tick()
    print(
        json.dumps(
            {
                "ts": res.ts.isoformat(),
                "equity": round(res.snapshot.equity, 2),
                "signals": res.signals,
                "fills": res.fills,
                "rejected": res.rejected,
                "errors": res.errors,
            },
            indent=2,
        )
    )
    return 0


def cmd_discover(c: Container, args: argparse.Namespace) -> int:
    found = c.listings.scan()
    print(json.dumps([n.model_dump(mode="json") for n in found], indent=2))
    return 0


def cmd_evolve(c: Container, args: argparse.Namespace) -> int:
    records = c.evolution.run(dry_run=args.dry_run)
    print(json.dumps([r.model_dump(mode="json") for r in records], indent=2, default=str))
    return 0


def cmd_report(c: Container, args: argparse.Namespace) -> int:
    text = (
        _send_report(c)
        if args.send
        else c.report.format(c.report.build(c.clock.now()), c.clock.now())
    )
    print(text)
    return 0


def _send_report(c: Container) -> str:
    now = c.clock.now()
    text = c.report.format(c.report.build(now), now)
    c.notifier.send(text)
    return text


def cmd_backtest(c: Container, args: argparse.Namespace) -> int:
    from trading_agent.backtest.simulator import run_backtest

    entry = c.registry.manifest.get(args.strategy)
    if entry is None:
        print(
            f"unknown strategy {args.strategy}; known: {[e.id for e in c.registry.manifest.entries]}",
            file=sys.stderr,
        )
        return 2
    strategy = c.registry.instantiate(entry)
    s = c.settings
    listings = c.listings.recent(c.clock.now(), timedelta(days=s.universe.new_listing_max_age_days))
    symbols = strategy.universe(s.universe.core_symbols, listings) or s.universe.core_symbols
    candles = {sym: c.candle_store.read(sym, s.universe.timeframe)[-args.bars :] for sym in symbols}
    candles = {k: v for k, v in candles.items() if v}
    if not candles:
        print(
            "no stored candles; run `trading-agent backfill` first (or use --synthetic tick a few times)",
            file=sys.stderr,
        )
        return 2
    res = run_backtest(
        strategy,
        candles,
        s.risk,
        s.exchange.fees,
        s.risk.initial_capital_quote,
        s.universe.timeframe,
        listings,
    )
    print(
        json.dumps(
            {
                "strategy": res.strategy_id,
                "symbols": list(candles),
                **res.metrics(),
                "rejections": res.rejections,
            },
            indent=2,
        )
    )
    return 0


def cmd_backfill(c: Container, args: argparse.Namespace) -> int:
    _backfill(c, args.bars)
    return 0


def _backfill(c: Container, bars: int) -> None:
    s = c.settings
    for sym in s.universe.core_symbols:
        cs = c.market_data.get_candles(sym, s.universe.timeframe, bars)
        n = c.candle_store.append(sym, s.universe.timeframe, cs)
        log.info("backfill", symbol=sym, fetched=len(cs), stored_new=n)


def cmd_status(c: Container, args: argparse.Namespace) -> int:
    state = c.engine.state
    print(
        json.dumps(
            {
                "health": c.health(),
                "ticks": state.ticks,
                "peak_equity": round(state.peak_equity, 2),
                "day": state.day.model_dump() if state.day else None,
                "strategies": [e.model_dump(mode="json") for e in c.registry.manifest.entries],
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_resume(c: Container, args: argparse.Namespace) -> int:
    c.engine.resume()
    print("halt cleared; peak equity reset")
    return 0


def cmd_check(c: Container, args: argparse.Namespace) -> int:
    from trading_agent.evolution.sandbox import check_source

    bad = 0
    for path in sorted(c.settings.generated_strategies_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        violations = check_source(path.read_text())
        print(f"{path.name}: {'OK' if not violations else violations}")
        bad += bool(violations)
    return 1 if bad else 0


COMMANDS = {
    "run": cmd_run,
    "tick": cmd_tick,
    "discover": cmd_discover,
    "evolve": cmd_evolve,
    "report": cmd_report,
    "backtest": cmd_backtest,
    "backfill": cmd_backfill,
    "status": cmd_status,
    "resume": cmd_resume,
    "check": cmd_check,
}


def _settings_for_tests() -> Settings:  # pragma: no cover - convenience
    return load_settings()


if __name__ == "__main__":
    sys.exit(main())
