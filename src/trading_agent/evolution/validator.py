"""ProposalValidator: static policy -> sandboxed backtest -> beat the baseline, or be rejected.

The candidate and the baseline are backtested on the *same* stored candles through the same
subprocess runner, so the comparison is apples to apples and the parent process never
executes untrusted code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from trading_agent.adapters.store.candle_store import CandleStore
from trading_agent.core.models import NewListing
from trading_agent.evolution.context import ResearchContext
from trading_agent.evolution.proposal import STRATEGY_ID_RE, Proposal
from trading_agent.evolution.sandbox import check_source
from trading_agent.ports import ClockPort
from trading_agent.settings import Settings
from trading_agent.strategies.registry import StrategyRegistry

log = structlog.get_logger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[3]


class ValidationResult(BaseModel):
    ok: bool
    stage: str
    reason: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    baseline_metrics: dict[str, Any] = Field(default_factory=dict)


class ProposalValidator:
    def __init__(
        self,
        settings: Settings,
        registry: StrategyRegistry,
        candle_store: CandleStore,
        clock: ClockPort,
        listings_provider: Callable[[], list[NewListing]] | None = None,
    ) -> None:
        self.s = settings
        self.registry = registry
        self.candles = candle_store
        self.clock = clock
        self._listings_provider = listings_provider or (lambda: [])

    @property
    def listings(self) -> list[NewListing]:
        return self._listings_provider()

    # ------------------------------------------------------------------ public
    def validate(self, proposal: Proposal, context: ResearchContext) -> ValidationResult:
        try:
            return self._validate(proposal, context)
        except Exception as exc:  # noqa: BLE001 - validation must always produce a verdict
            log.error("validation_error", proposal=proposal.id, error=str(exc))
            return ValidationResult(ok=False, stage="error", reason=f"{type(exc).__name__}: {exc}")

    # ----------------------------------------------------------------- private
    def _validate(self, p: Proposal, ctx: ResearchContext) -> ValidationResult:
        if p.kind == "no_change":
            return ValidationResult(ok=True, stage="noop", reason="researcher proposed no change")
        if not STRATEGY_ID_RE.match(p.strategy_id):
            return ValidationResult(
                ok=False, stage="schema", reason=f"invalid strategy_id {p.strategy_id!r}"
            )

        if p.kind == "retire_strategy":
            return self._validate_retire(p, ctx)

        if p.kind == "new_strategy":
            if self.registry.manifest.get(p.strategy_id):
                return ValidationResult(
                    ok=False, stage="schema", reason=f"strategy id {p.strategy_id} already exists"
                )
            if not p.class_name.isidentifier():
                return ValidationResult(
                    ok=False, stage="schema", reason="class_name is not an identifier"
                )
            violations = check_source(p.source_code)
            if violations:
                return ValidationResult(ok=False, stage="policy", reason="; ".join(violations[:8]))
            candidate_job = {
                "mode": "source",
                "source": p.source_code,
                "class_name": p.class_name,
                "params": {},
            }
            baseline_entry = self.registry.champion()
        elif p.kind == "tune_params":
            entry = self.registry.manifest.get(p.strategy_id)
            if entry is None or entry.status == "retired":
                return ValidationResult(
                    ok=False, stage="schema", reason=f"unknown or retired strategy {p.strategy_id}"
                )
            try:
                new_params = p.params()
            except (ValueError, json.JSONDecodeError) as exc:
                return ValidationResult(ok=False, stage="schema", reason=f"bad params_json: {exc}")
            if not new_params or new_params == entry.params:
                return ValidationResult(
                    ok=False, stage="schema", reason="tune_params with empty or identical params"
                )
            candidate_job = self._job_for_entry(entry, {**entry.params, **new_params})
            baseline_entry = entry
        else:
            return ValidationResult(ok=False, stage="schema", reason=f"unknown kind {p.kind}")

        data_path, bars, symbols = self._write_dataset()
        try:
            if bars < self.s.evolution.min_backtest_bars:
                return ValidationResult(
                    ok=False,
                    stage="data",
                    reason=f"only {bars} stored bars; need {self.s.evolution.min_backtest_bars}",
                )
            cand = self._run(candidate_job, data_path)
            if not cand.get("ok"):
                return ValidationResult(
                    ok=False, stage="backtest", reason=f"candidate failed: {cand.get('error')}"
                )
            base: dict[str, Any] = {"ok": True, "metrics": {}}
            if baseline_entry is not None:
                base = self._run(
                    self._job_for_entry(baseline_entry, baseline_entry.params), data_path
                )
                if not base.get("ok"):
                    log.warning("baseline_backtest_failed", error=base.get("error"))
                    base = {"ok": True, "metrics": {}}
        finally:
            Path(data_path).unlink(missing_ok=True)

        return self._gate(cand["metrics"], base["metrics"], symbols)

    def _gate(
        self, cand: dict[str, Any], base: dict[str, Any], symbols: list[str]
    ) -> ValidationResult:
        e = self.s.evolution
        ret, dd, trades = (
            cand.get("total_return_pct", 0.0),
            cand.get("max_drawdown_pct", 100.0),
            cand.get("trades", 0),
        )
        base_ret = base.get("total_return_pct", 0.0)
        if trades < e.min_backtest_trades:
            return ValidationResult(
                ok=False,
                stage="gate",
                reason=f"only {trades} trades in backtest (min {e.min_backtest_trades})",
                metrics=cand,
                baseline_metrics=base,
            )
        if dd > e.max_backtest_drawdown_pct:
            return ValidationResult(
                ok=False,
                stage="gate",
                reason=f"max drawdown {dd:.1f}% > {e.max_backtest_drawdown_pct}%",
                metrics=cand,
                baseline_metrics=base,
            )
        if e.require_positive_return and ret <= 0:
            return ValidationResult(
                ok=False,
                stage="gate",
                reason=f"net return after fees {ret:.2f}% is not positive",
                metrics=cand,
                baseline_metrics=base,
            )
        if ret < base_ret + e.required_improvement_pct:
            return ValidationResult(
                ok=False,
                stage="gate",
                reason=f"return {ret:.2f}% does not beat baseline {base_ret:.2f}% by {e.required_improvement_pct}pp",
                metrics=cand,
                baseline_metrics=base,
            )
        if cand.get("fees_pct_of_start_equity", 0.0) > e.max_backtest_fees_pct:
            return ValidationResult(
                ok=False,
                stage="gate",
                reason=f"fees {cand['fees_pct_of_start_equity']:.2f}% of capital exceed {e.max_backtest_fees_pct}%",
                metrics=cand,
                baseline_metrics=base,
            )
        return ValidationResult(
            ok=True,
            stage="gate",
            reason=f"beat baseline on {len(symbols)} symbols: {ret:.2f}% vs {base_ret:.2f}%, dd {dd:.1f}%, {trades} trades",
            metrics=cand,
            baseline_metrics=base,
        )

    def _validate_retire(self, p: Proposal, ctx: ResearchContext) -> ValidationResult:
        entry = self.registry.manifest.get(p.strategy_id)
        if entry is None or entry.status == "retired":
            return ValidationResult(
                ok=False, stage="schema", reason="unknown or already retired strategy"
            )
        if entry.status == "champion":
            return ValidationResult(
                ok=False, stage="gate", reason="cannot retire the champion; promote something first"
            )
        perf = ctx.performance_of(p.strategy_id)
        age = ctx.generated_at - entry.since
        if age < timedelta(days=self.s.evolution.promotion_min_days):
            return ValidationResult(
                ok=False,
                stage="gate",
                reason=f"strategy is only {age.days}d old; needs {self.s.evolution.promotion_min_days}d of live data",
            )
        if perf and perf.return_pct is not None and perf.return_pct < 0:
            return ValidationResult(
                ok=True,
                stage="gate",
                reason=f"live return {perf.return_pct:.2f}% over {ctx.lookback_days}d",
            )
        if perf and perf.trades == 0 and perf.signals == 0:
            return ValidationResult(
                ok=True, stage="gate", reason="strategy produced no signals in the lookback window"
            )
        return ValidationResult(
            ok=False, stage="gate", reason="live performance does not justify retirement"
        )

    def _job_for_entry(self, entry, params: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        if entry.origin == "builtin":
            return {
                "mode": "module",
                "module": entry.module,
                "class_name": entry.class_name,
                "params": params,
            }
        return {
            "mode": "file",
            "path": str(self.s.generated_strategies_dir / entry.module),
            "class_name": entry.class_name,
            "params": params,
        }

    def _write_dataset(self) -> tuple[str, int, list[str]]:
        tf = self.s.universe.timeframe
        symbols = list(self.s.universe.core_symbols)
        recent = {n.symbol for n in self.listings}
        symbols += [s for s in self.candles.instruments(tf) if s in recent and s not in symbols]
        data: dict[str, list[dict[str, Any]]] = {}
        bars = 0
        for sym in symbols:
            cs = self.candles.read(sym, tf)[-self.s.evolution.max_backtest_bars :]
            if cs:
                data[sym] = [c.model_dump(mode="json") for c in cs]
                bars = max(bars, len(cs))
        fd, path = tempfile.mkstemp(prefix="ta_backtest_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "candles": data,
                    "new_listings": [n.model_dump(mode="json") for n in self.listings],
                },
                fh,
            )
        return path, bars, list(data)

    def _run(self, job: dict[str, Any], data_path: str) -> dict[str, Any]:
        full = {
            **job,
            "data_path": data_path,
            "fees": {k: v.model_dump() for k, v in self.s.exchange.fees.items()},
            "risk": self.s.risk.model_dump(),
            "initial_cash": self.s.risk.initial_capital_quote
            * self.s.evolution.challenger_allocation_pct
            / 100.0,
            "timeframe": self.s.universe.timeframe,
            "memory_mb": self.s.evolution.subprocess_memory_mb,
            "cpu_seconds": self.s.evolution.subprocess_timeout_s,
        }
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": tempfile.gettempdir(),
        }
        try:
            proc = subprocess.run(
                [sys.executable, "-s", "-B", "-m", "trading_agent.evolution.runner"],
                input=json.dumps(full),
                capture_output=True,
                text=True,
                timeout=self.s.evolution.subprocess_timeout_s + 15,
                env=env,
                cwd=tempfile.gettempdir(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "backtest timed out"}
        try:
            return (
                json.loads(proc.stdout.strip().splitlines()[-1])
                if proc.stdout.strip()
                else {"ok": False, "error": proc.stderr[-800:] or "no output"}
            )
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": f"runner produced invalid output: {proc.stdout[-400:]} {proc.stderr[-400:]}",
            }
