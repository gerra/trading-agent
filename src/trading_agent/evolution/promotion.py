"""Promotion review: challengers earn the champion label with *live* paper results, not backtests."""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog

from trading_agent.core.events import EvolutionRecord
from trading_agent.evolution.performance import strategy_performance
from trading_agent.ports import EventStorePort
from trading_agent.settings import Settings
from trading_agent.strategies.registry import StrategyRegistry

log = structlog.get_logger(__name__)


class PromotionReviewer:
    def __init__(
        self, settings: Settings, registry: StrategyRegistry, store: EventStorePort
    ) -> None:
        self.s = settings
        self.registry = registry
        self.store = store

    def review(self, now: datetime) -> list[EvolutionRecord]:
        e = self.s.evolution
        records: list[EvolutionRecord] = []
        champion = self.registry.champion()
        if champion is None:
            return records
        window = timedelta(days=e.promotion_min_days)
        champ_perf = strategy_performance(self.store, champion.id, since=now - window)
        champ_ret = champ_perf.return_pct if champ_perf.return_pct is not None else 0.0

        for entry in list(self.registry.active_entries()):
            if entry.status != "challenger" or now - entry.since < window:
                continue
            perf = strategy_performance(self.store, entry.id, since=now - window)
            ret = perf.return_pct
            if ret is None:
                continue
            metrics = {
                "return_pct": round(ret, 3),
                "trades": perf.trades,
                "champion_return_pct": round(champ_ret, 3),
                "window_days": e.promotion_min_days,
            }
            if (
                perf.trades >= e.min_live_trades_for_promotion
                and ret >= champ_ret + e.promote_margin_pct
                and ret > 0
            ):
                self.registry.set_status(champion.id, "challenger")
                self.registry.set_status(entry.id, "champion")
                records.append(
                    EvolutionRecord(
                        proposal_id=entry.proposal_id or "builtin",
                        kind="promotion",
                        strategy_id=entry.id,
                        outcome="promoted",
                        reason=f"live {ret:+.2f}% vs champion {champ_ret:+.2f}% over {e.promotion_min_days}d",
                        metrics=metrics,
                    )
                )
                log.info("strategy_promoted", strategy_id=entry.id, **metrics)
                champion, champ_ret = entry, ret
            elif ret <= -e.retire_loss_pct or ret <= champ_ret - e.retire_margin_pct:
                self.registry.set_status(entry.id, "retired", allocation_pct=0)
                records.append(
                    EvolutionRecord(
                        proposal_id=entry.proposal_id or "builtin",
                        kind="promotion",
                        strategy_id=entry.id,
                        outcome="demoted",
                        reason=f"live {ret:+.2f}% (champion {champ_ret:+.2f}%) over {e.promotion_min_days}d; retired",
                        metrics=metrics,
                    )
                )
                log.info("strategy_retired", strategy_id=entry.id, **metrics)
        return records
