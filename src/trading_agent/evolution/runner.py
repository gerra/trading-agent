"""Subprocess entry point: backtest one candidate strategy under resource limits.

Invoked by validator.py as `python -m trading_agent.evolution.runner` with a JSON job on stdin.
Prints a single JSON object on stdout. Never imports adapters, never touches the network.
"""

from __future__ import annotations

import json
import resource
import sys
import traceback
from datetime import datetime
from typing import Any

from trading_agent.core.fees import FeeSchedule
from trading_agent.core.models import Candle, NewListing
from trading_agent.settings import RiskSettings


def _limit(job: dict[str, Any]) -> None:
    mem = int(job.get("memory_mb", 512)) * 1024 * 1024
    cpu = int(job.get("cpu_seconds", 90))
    for res, val in (
        (resource.RLIMIT_AS, mem),
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_FSIZE, 1024 * 1024),
    ):
        try:
            soft, hard = resource.getrlimit(res)
            resource.setrlimit(
                res, (val, hard if hard != resource.RLIM_INFINITY and hard < val else val)
            )
        except (ValueError, OSError):
            pass


def _load_strategy(job: dict[str, Any]):  # type: ignore[no-untyped-def]
    from trading_agent.strategies.registry import load_class_from_source

    params = job.get("params") or {}
    if job["mode"] == "source":
        from trading_agent.evolution.sandbox import check_source

        violations = check_source(job["source"])
        if violations:
            raise PermissionError(f"policy violations: {violations}")
        cls = load_class_from_source(job["source"], job.get("class_name") or None)
        return cls(params)
    if job["mode"] == "module":
        import importlib

        module = importlib.import_module(job["module"])
        cls = getattr(module, job["class_name"])
        return cls(params)
    if job["mode"] == "file":
        from trading_agent.evolution.sandbox import check_source

        source = open(job["path"], encoding="utf-8").read()  # noqa: SIM115
        violations = check_source(source)
        if violations:
            raise PermissionError(f"policy violations: {violations}")
        cls = load_class_from_source(source, job.get("class_name") or None)
        return cls(params)
    raise ValueError(f"unknown mode {job['mode']}")


def run(job: dict[str, Any]) -> dict[str, Any]:
    from trading_agent.backtest.simulator import run_backtest

    strategy = _load_strategy(job)
    with open(job["data_path"], encoding="utf-8") as fh:
        raw = json.load(fh)
    candles = {sym: [Candle.model_validate(c) for c in cs] for sym, cs in raw["candles"].items()}
    listings = [NewListing.model_validate(n) for n in raw.get("new_listings", [])]
    fees = {k: FeeSchedule.model_validate(v) for k, v in job["fees"].items()}
    result = run_backtest(
        strategy,
        candles,
        RiskSettings.model_validate(job["risk"]),
        fees,
        initial_cash=float(job["initial_cash"]),
        timeframe=job["timeframe"],
        new_listings=listings,
    )
    return {
        "ok": True,
        "strategy_id": strategy.id,
        "metrics": {**result.metrics(), "rejections": result.rejections},
    }


def main() -> int:
    job = json.load(sys.stdin)
    _limit(job)
    try:
        out = run(job)
    except MemoryError:
        out = {"ok": False, "error": "memory limit exceeded"}
    except Exception as exc:  # noqa: BLE001
        out = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-2000:],
        }
    sys.stdout.write(json.dumps(out, default=_json_default))
    sys.stdout.flush()
    return 0 if out.get("ok") else 1


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


if __name__ == "__main__":
    sys.exit(main())
