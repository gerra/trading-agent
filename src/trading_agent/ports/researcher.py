"""The LLM-facing port used by the evolution loop.

Splitting research (free-form, may use web search) from proposal (strictly structured) keeps
the dangerous step - code generation - small, typed and easy to validate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # avoids a ports <-> evolution import cycle; annotations are strings at runtime
    from trading_agent.evolution.context import ResearchContext
    from trading_agent.evolution.proposal import ProposalDraft, ResearchNotes


class ResearcherPort(Protocol):
    def research(self, context: ResearchContext) -> ResearchNotes: ...

    def propose(self, context: ResearchContext, notes: ResearchNotes) -> ProposalDraft: ...
