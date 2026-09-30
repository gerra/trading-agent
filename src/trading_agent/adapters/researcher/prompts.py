"""Prompts for the research and proposal steps. Kept in one file so they are easy to review.

The system prompts are static (cacheable). All per-run data goes into the user message.
"""

from __future__ import annotations

from trading_agent.evolution.context import ResearchContext

RESEARCH_SYSTEM = """You are the research analyst for a small, fully automated crypto paper-trading system.
The system trades spot only, long only, on a retail account with real fees and slippage. It is
evaluated on net return AFTER fees, max drawdown and consistency - not on gross activity.

Your job today: find concrete, evidence-backed improvements to the strategies below. Search the
web for recent, credible material (papers, reputable quant blogs, exchange fee changes, new-listing
behaviour studies). Prefer robust, simple, low-turnover ideas over clever ones. Fees kill.

Write compact markdown notes:
1. What the data says about current strategies (use the performance and rejection stats given).
2. 3-5 findings from research, each with why it matters here and a source URL.
3. 2-3 candidate changes ranked by expected value, each with a falsifiable hypothesis.
Keep it under 900 words. Do not write code in this step."""

PROPOSE_SYSTEM = """You are the strategy engineer for a small automated crypto paper-trading system.
You receive research notes and the full current context, and must return exactly ONE proposal.

Choose the single highest expected-value change:
- new_strategy: a complete Python module following the contract and policy exactly.
- tune_params: JSON params for an existing strategy (only keys that strategy already has).
- retire_strategy: a losing challenger (never the champion).
- no_change: when nothing beats the bar; say why. This is a perfectly good answer.

Hard rules for new_strategy code:
- Obey the policy text verbatim. Any violation is rejected automatically without a retry today.
- expected_edge_bps on buy signals must be a real estimate; the risk manager rejects entries whose
  edge is below a multiple of the round-trip cost. Low-turnover ideas win.
- Every open position must have an exit rule (stop, target, time, or signal reversal).
- Keep it under 150 lines, deterministic, and readable by a human in two minutes.
- The class id ClassVar must equal strategy_id and the file name will be <strategy_id>.py.
Do not repeat a proposal that was already rejected for the same reason."""


def research_user_message(ctx: ResearchContext) -> str:
    return "\n\n".join(
        [
            f"# Context ({ctx.generated_at.date()}, lookback {ctx.lookback_days} days)",
            _portfolio_block(ctx),
            _strategies_block(ctx),
            _market_block(ctx),
            _history_block(ctx),
            "# Policy for generated code\n" + ctx.policy,
        ]
    )


def propose_user_message(ctx: ResearchContext, notes_markdown: str) -> str:
    generated = (
        "\n\n".join(
            f"## generated/{sid}.py\n```python\n{src}\n```"
            for sid, src in ctx.generated_sources.items()
        )
        or "(none yet)"
    )
    return "\n\n".join(
        [
            research_user_message(ctx),
            "# Research notes\n" + notes_markdown,
            "# Strategy contract (trading_agent/strategies/base.py)\n```python\n"
            + ctx.strategy_interface_source
            + "\n```",
            "# Available indicators (trading_agent/strategies/indicators.py)\n```python\n"
            + ctx.indicators_source
            + "\n```",
            "# Example strategy (builtin/trend_breakout.py)\n```python\n"
            + ctx.example_strategy_source
            + "\n```",
            "# Existing generated strategies\n" + generated,
            "Return the single best proposal now.",
        ]
    )


def _portfolio_block(ctx: ResearchContext) -> str:
    eq = f"{ctx.portfolio_equity:.2f}" if ctx.portfolio_equity is not None else "n/a"
    ret = f"{ctx.portfolio_return_pct:+.2f}%" if ctx.portfolio_return_pct is not None else "n/a"
    return (
        "# Portfolio\n"
        f"- equity: {eq} (since start: {ret}), fees paid total: {ctx.fees_paid_total:.2f}\n"
        f"- costs: {ctx.fee_schedule_text}\n"
        f"- risk limits: {ctx.risk_limits}"
    )


def _strategies_block(ctx: ResearchContext) -> str:
    lines = ["# Strategies (live paper results over the lookback)"]
    for s in ctx.strategies:
        ret = f"{s.return_pct:+.2f}%" if s.return_pct is not None else "n/a"
        wr = f"{s.win_rate * 100:.0f}%" if s.win_rate is not None else "n/a"
        dd = f"{s.max_drawdown_pct:.2f}%" if s.max_drawdown_pct is not None else "n/a"
        lines.append(
            f"- {s.strategy_id} [{s.status}, alloc {s.allocation_pct}%] return {ret}, maxDD {dd}, trades {s.trades}, "
            f"win {wr}, fees {s.fees_paid:.2f}, signals {s.signals}, open {s.open_positions}, params {s.params}, "
            f"rejections {s.rejection_reasons}"
        )
    return "\n".join(lines)


def _market_block(ctx: ResearchContext) -> str:
    market = "\n".join(f"- {m}" for m in ctx.market_summary) or "- no stored candles yet"
    listings = ", ".join(ctx.recent_listings) or "none"
    return f"# Market data on disk\n{market}\n\nRecent new listings: {listings}"


def _history_block(ctx: ResearchContext) -> str:
    if not ctx.past_proposals:
        return "# Past proposals\n(none)"
    lines = ["# Past proposals (newest last)"]
    for p in ctx.past_proposals:
        lines.append(f"- {p.ts.date()} {p.kind} {p.strategy_id}: {p.outcome} - {p.reason}")
    return "\n".join(lines)
