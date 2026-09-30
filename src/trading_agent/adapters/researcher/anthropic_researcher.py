"""ResearcherPort implementation on the Claude API.

Two calls per iteration:
1. research(): web search enabled, free-form markdown notes.
2. propose(): structured output (ProposalDraft), no tools.
Both opt into server-side refusal fallbacks and handle `pause_turn` and `refusal` explicitly.
"""

from __future__ import annotations

from typing import Any

import anthropic
import structlog

from trading_agent.adapters.researcher.prompts import (
    PROPOSE_SYSTEM,
    RESEARCH_SYSTEM,
    propose_user_message,
    research_user_message,
)
from trading_agent.evolution.context import ResearchContext
from trading_agent.evolution.proposal import ProposalDraft, ResearchNotes

log = structlog.get_logger(__name__)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ResearchRefusedError(RuntimeError):
    pass


class AnthropicResearcher:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        effort: str,
        web_search_max_uses: int,
        timeout_s: float = 900.0,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=3)
        self._model = model
        self._effort = effort
        self._web_search_max_uses = web_search_max_uses

    def research(self, context: ResearchContext) -> ResearchNotes:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": research_user_message(context)}
        ]
        tools: list[dict[str, Any]] = [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": self._web_search_max_uses,
            }
        ]
        text_parts: list[str] = []
        sources: list[str] = []
        for _ in range(6):  # pause_turn continuations
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=16000,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config={"effort": self._effort},
                system=[
                    {
                        "type": "text",
                        "text": RESEARCH_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=tools,
                messages=messages,
            )
            if response.stop_reason == "refusal":
                raise ResearchRefusedError(_refusal_text(response))
            for block in response.content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    text_parts.append(block.text)
                elif btype == "web_search_tool_result":
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        sources.extend(
                            getattr(r, "url", "") for r in content if getattr(r, "url", "")
                        )
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
        _log_usage("research", response)
        return ResearchNotes(
            markdown="\n".join(text_parts).strip() or "(no notes returned)",
            sources=sorted(set(sources))[:20],
        )

    def propose(self, context: ResearchContext, notes: ResearchNotes) -> ProposalDraft:
        response = self._client.beta.messages.parse(
            model=self._model,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": self._effort},
            system=[
                {"type": "text", "text": PROPOSE_SYSTEM, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": propose_user_message(context, notes.markdown)}],
            output_format=ProposalDraft,
        )
        if response.stop_reason == "refusal":
            raise ResearchRefusedError(_refusal_text(response))
        _log_usage("propose", response)
        draft = response.parsed_output
        if draft is None:
            raise ValueError("model returned no parsable proposal")
        return draft


def _refusal_text(response: Any) -> str:
    details = getattr(response, "stop_details", None)
    return f"model refused: {getattr(details, 'category', None)} - {getattr(details, 'explanation', '')}"


def _log_usage(step: str, response: Any) -> None:
    u = getattr(response, "usage", None)
    if u is not None:
        log.info(
            "llm_usage",
            step=step,
            model=getattr(response, "model", None),
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read=getattr(u, "cache_read_input_tokens", 0),
        )
