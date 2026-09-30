from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from trading_agent.core.events import JobRunRecord, job_run_event
from trading_agent.observability.metrics import Metrics
from trading_agent.ports import ClockPort, EventStorePort, NotifierPort

log = structlog.get_logger(__name__)


class JobRunner:
    """Wraps every scheduled job: timing, job_run event, metrics, alert on failure (rate limited)."""

    def __init__(
        self,
        store: EventStorePort,
        metrics: Metrics,
        notifier: NotifierPort,
        clock: ClockPort,
        alert_cooldown_s: int = 3600,
    ) -> None:
        self.store = store
        self.metrics = metrics
        self.notifier = notifier
        self.clock = clock
        self._last_alert: dict[str, float] = {}
        self._cooldown = alert_cooldown_s

    def wrap(self, name: str, fn: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            t0 = time.monotonic()
            ok, err = True, None
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a job failure must not stop the scheduler
                ok, err = False, f"{type(exc).__name__}: {exc}"
                log.exception("job_failed", job=name)
                self._alert(name, err)
            duration = time.monotonic() - t0
            self.metrics.job_runs.labels(job=name, ok=str(ok).lower()).inc()
            self.metrics.job_duration.labels(job=name).observe(duration)
            self.store.append(
                job_run_event(
                    self.clock.now(),
                    JobRunRecord(job=name, ok=ok, duration_s=round(duration, 3), error=err),
                )
            )

        return run

    def _alert(self, name: str, err: str) -> None:
        now = time.monotonic()
        if now - self._last_alert.get(name, -1e9) < self._cooldown:
            return
        self._last_alert[name] = now
        self.notifier.send(f"❗ job '{name}' failed: {err[:300]}")


def build_scheduler(
    runner: JobRunner,
    tick: Callable[[], Any],
    discover: Callable[[], Any],
    evolve: Callable[[], Any],
    report: Callable[[], Any],
    tick_minutes: int,
    discovery_minutes: int,
    evolution_hour_utc: int,
    report_hour: int,
    report_minute: int,
    report_tz: str,
    evolution_enabled: bool,
) -> BlockingScheduler:
    sched = BlockingScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
    )
    sched.add_job(
        runner.wrap("tick", tick),
        IntervalTrigger(minutes=tick_minutes),
        id="tick",
        name="trading tick",
    )
    sched.add_job(
        runner.wrap("discovery", discover),
        IntervalTrigger(minutes=discovery_minutes),
        id="discovery",
        name="new listing discovery",
    )
    if evolution_enabled:
        sched.add_job(
            runner.wrap("evolve", evolve),
            CronTrigger(hour=evolution_hour_utc, minute=17, timezone="UTC"),
            id="evolve",
            name="daily evolution",
        )
    sched.add_job(
        runner.wrap("report", report),
        CronTrigger(hour=report_hour, minute=report_minute, timezone=report_tz),
        id="report",
        name="daily telegram report",
    )
    return sched
