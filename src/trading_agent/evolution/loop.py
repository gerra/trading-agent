"""EvolutionLoop.run(): one self-improvement iteration. Safe to run any time; idempotent per day."""

from __future__ import annotations

import uuid
from datetime import timedelta

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

        # 2) Daily budget of LLM-driven proposals.
        today = [
            ev
            for ev in self.store.read("evolution", since=now - timedelta(hours=24))
            if ev.payload.get("kind") != "promotion"
        ]
        if len(today) >= self.s.evolution.max_proposals_per_day:
            log.info("evolution_budget_exhausted", proposals_today=len(today))
            return records

        # 3) Research -> propose. Any failure here is recorded, never raised into the scheduler.
        context = self.context_builder.build()
        proposal_id = f"p{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        try:
            notes = self.researcher.research(context)
            self._save_notes(proposal_id, notes.markdown, notes.sources)
            draft = self.researcher.propose(context, notes)
            proposal = Proposal.from_draft(draft, proposal_id)
        except Exception as exc:  # noqa: BLE001
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
