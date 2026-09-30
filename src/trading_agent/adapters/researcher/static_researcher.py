"""Offline researcher: used when no API key is configured and in tests.

It proposes `no_change`, so the loop still runs end to end (promotion review, ledger, report)
without ever calling an LLM.
"""

from __future__ import annotations

from trading_agent.evolution.context import ResearchContext
from trading_agent.evolution.proposal import ProposalDraft, ResearchNotes


class StaticResearcher:
    def __init__(self, draft: ProposalDraft | None = None) -> None:
        self._draft = draft

    def research(self, context: ResearchContext) -> ResearchNotes:
        return ResearchNotes(
            markdown="Offline mode: no research performed (ANTHROPIC_API_KEY not set).", sources=[]
        )

    def propose(self, context: ResearchContext, notes: ResearchNotes) -> ProposalDraft:
        return self._draft or ProposalDraft(
            kind="no_change",
            strategy_id="none",
            class_name="",
            source_code="",
            params_json="{}",
            rationale="Offline mode: no LLM available, so no change proposed.",
            hypothesis="",
            expected_improvement="",
            risks="",
        )
