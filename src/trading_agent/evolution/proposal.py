"""What the researcher must return. Kept flat and fully required so structured outputs work."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

ProposalKind = Literal["new_strategy", "tune_params", "retire_strategy", "no_change"]
STRATEGY_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


class ResearchNotes(BaseModel):
    markdown: str
    sources: list[str] = Field(default_factory=list)


class ProposalDraft(BaseModel):
    """LLM output schema. Every field is required; use empty strings when not applicable."""

    kind: ProposalKind
    strategy_id: str = Field(description="snake_case id of the new or target strategy")
    class_name: str = Field(description="CapWords class name; empty unless kind=new_strategy")
    source_code: str = Field(description="full Python module; empty unless kind=new_strategy")
    params_json: str = Field(description="JSON object of params; '{}' unless kind=tune_params")
    rationale: str = Field(description="why this change, 2-4 sentences, plain language")
    hypothesis: str = Field(
        description="one falsifiable sentence: what should improve and how it will be measured"
    )
    expected_improvement: str = Field(description="what metric should move and roughly by how much")
    risks: str = Field(description="what could go wrong")


class Proposal(ProposalDraft):
    id: str

    def params(self) -> dict[str, Any]:
        raw = self.params_json.strip() or "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("params_json must decode to an object")
        return data

    def short(self) -> str:
        return f"{self.kind}:{self.strategy_id}"

    @classmethod
    def from_draft(cls, draft: ProposalDraft, proposal_id: str) -> Proposal:
        return cls(id=proposal_id, **draft.model_dump())
