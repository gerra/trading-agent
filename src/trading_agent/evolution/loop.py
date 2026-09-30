"""EvolutionLoop.run(): one self-improvement iteration. Safe to run any time; idempotent per day."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from trading_agent.core.events import EvolutionRecord, evolution_event
from trading_agent.evolution.applier import ProposalApplier
from trading_agent.evolution.context import ContextBuilder
from trading_agent.evolution.promotion import PromotionReviewer
from trading_agent.evolution.proposal import Proposal
from trading_agent.evolution.validator import ProposalValidator
from trading_agent.observability.metrics import Metrics
from trading_agent.ports import ClockPort, EventStorePort, ResearcherPort
from trading_agent.settings import Settings

log = structlog.get_logger(__name__)


class EvolutionLoop:
    def __init__(
        self,
        settings: Settings,
        context_builder: ContextBuilder,
        researcher: ResearcherPort,
        validator: ProposalValidator,
        applier: ProposalApplier,
        reviewer: PromotionReviewer,
        store: EventStorePort,
        clock: ClockPort,
        metrics: Metrics,
    ) -> None:
        self.s = settings
        self.context_builder = context_builder
        self.researcher = researcher
        self.validator = validator
        self.applier = applier
        self.reviewer = reviewer
        self.store = store
        self.clock = clock
        self.metrics = metrics

    def run(self, dry_run: bool = False) -> list[EvolutionRecord]:
        now = self.clock.now()
        records: list[EvolutionRecord] = []

        # 1) Promote / retire based on live results - cheap, no LLM involved.
        for rec in self.reviewer.review(now):
            self._record(rec)
            records.append(rec)

        # 2) Should we spend an LLM call at all today?
        skip = self._skip_reason(now)
        if skip:
            log.info("evolution_skipped", reason=skip)
            return records

        # 3) Research (web search, at most every research_interval_days) -> propose (one call).
        context = self.context_builder.build()
        proposal_id = f"p{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        try:
            notes = self._notes(context, proposal_id, now)
            self.metrics.llm_calls.labels(step="propose").inc()
            draft = self.researcher.propose(context, notes)
            proposal = Proposal.from_draft(draft, proposal_id)
        except Exception as exc:  # noqa: BLE001 - never raise into the scheduler
            rec = EvolutionRecord(
                proposal_id=proposal_id,
                kind="research",
                strategy_id="-",
                outcome="error",
                reason=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            self._record(rec)
            return records + [rec]

        # 4) Validate in the sandbox, then apply or reject.
        validation = self.validator.validate(proposal, context)
        base = dict(
            proposal_id=proposal.id,
            kind=proposal.kind,
            strategy_id=proposal.strategy_id,
            rationale=proposal.rationale[:600],
            hypothesis=proposal.hypothesis[:300],
            metrics=validation.metrics,
            baseline_metrics=validation.baseline_metrics,
        )
        if proposal.kind == "no_change":
            rec = EvolutionRecord(**base, outcome="no_change", reason=proposal.rationale[:300])
        elif not validation.ok:
            rec = EvolutionRecord(
                **base, outcome="rejected", reason=f"[{validation.stage}] {validation.reason}"
            )
        elif dry_run:
            rec = EvolutionRecord(
                **base, outcome="no_change", reason=f"dry run - would apply: {validation.reason}"
            )
        else:
            try:
                change = self.applier.apply(proposal, validation)
                rec = EvolutionRecord(
                    **base,
                    outcome="applied",
                    reason=validation.reason,
                    file_path=change.file_path,
                    commit=change.commit,
                )
            except Exception as exc:  # noqa: BLE001
                if proposal.kind == "new_strategy":
                    self.applier.rollback_new(proposal)
                rec = EvolutionRecord(
                    **base,
                    outcome="rolled_back",
                    reason=f"apply failed: {type(exc).__name__}: {str(exc)[:300]}",
                )
        self._record(rec)
        records.append(rec)
        return records

    # ------------------------------------------------------------ LLM budget
    def _skip_reason(self, now: datetime) -> str | None:
        """Reasons not to call the LLM today. Each one saves a full research/propose round."""
        e = self.s.evolution
        llm_records = [
            ev for ev in self.store.read("evolution") if ev.payload.get("kind") != "promotion"
        ]
        today = [ev for ev in llm_records if ev.ts >= now - timedelta(hours=24)]
        if len(today) >= e.max_proposals_per_day:
            return f"daily budget used ({len(today)}/{e.max_proposals_per_day})"
        if llm_records:
            age = now - llm_records[-1].ts
            if age < timedelta(days=e.min_days_between_proposals):
                return f"last proposal {age.days}d ago; min gap {e.min_days_between_proposals}d"
        if e.wait_for_pending_challenger:
            pending = [
                entry
                for entry in self.validator.registry.active_entries()
                if entry.status == "challenger"
                and entry.origin == "generated"
                and now - entry.since < timedelta(days=e.promotion_min_days)
            ]
            if pending:
                return f"challenger {pending[0].id} still under live evaluation ({(now - pending[0].since).days}d/{e.promotion_min_days}d)"
        return None

    def _notes(self, context, proposal_id: str, now: datetime):  # type: ignore[no-untyped-def]
        """Reuse the last research notes unless they are older than research_interval_days."""
        from trading_agent.evolution.proposal import ResearchNotes

        latest = self._latest_notes(now)
        if latest is not None:
            log.info("research_notes_reused", path=str(latest[0]))
            return ResearchNotes(markdown=latest[1], sources=[])
        self.metrics.llm_calls.labels(step="research").inc()
        notes = self.researcher.research(context)
        self._save_notes(proposal_id, notes.markdown, notes.sources)
        return notes

    def _latest_notes(self, now: datetime) -> tuple[Path, str] | None:
        files = sorted(self.s.research_dir.glob("*.md"))
        if not files:
            return None
        newest = files[-1]
        try:
            written = datetime.fromtimestamp(newest.stat().st_mtime, tz=UTC)
        except OSError:
            return None
        if now - written > timedelta(days=self.s.evolution.research_interval_days):
            return None
        text = newest.read_text(encoding="utf-8")
        return (newest, text) if text.strip() else None

    def _record(self, rec: EvolutionRecord) -> None:
        self.store.append(evolution_event(self.clock.now(), rec))
        self.metrics.evolution.labels(outcome=rec.outcome).inc()
        log.info(
            "evolution",
            proposal=rec.proposal_id,
            kind=rec.kind,
            strategy=rec.strategy_id,
            outcome=rec.outcome,
            reason=rec.reason[:200],
        )

    def _save_notes(self, proposal_id: str, markdown: str, sources: list[str]) -> None:
        path = self.s.research_dir / f"{self.clock.now().strftime('%Y-%m-%d')}_{proposal_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = markdown.strip() + (
            "\n\n## Sources\n" + "\n".join(f"- {s}" for s in sources) if sources else ""
        )
        path.write_text(body + "\n", encoding="utf-8")
