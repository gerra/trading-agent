from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_agent.core.metrics import round_trips
from trading_agent.core.models import Fill
from trading_agent.engine.state import EngineStateRepository
from trading_agent.evolution.performance import portfolio_equity_series, sleeve_equity_series
from trading_agent.ports import EventStorePort
from trading_agent.settings import Settings
from trading_agent.strategies.registry import StrategyRegistry


class DailyReportBuilder:
    def __init__(
        self,
        settings: Settings,
        store: EventStorePort,
        registry: StrategyRegistry,
        state_repo: EngineStateRepository,
    ) -> None:
        self.s = settings
        self.store = store
        self.registry = registry
        self.state_repo = state_repo

    def build(self, now: datetime) -> list[str]:
        """Return at most `report.max_bullets` short bullets. Order = importance."""
        tz = ZoneInfo(self.s.report.timezone)
        local_now = now.astimezone(tz)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
            now.tzinfo
        )
        bullets: list[str] = []

        series = portfolio_equity_series(self.store, since=None)
        state = self.state_repo.load()
        if series:
            eq = series[-1][1]
            start_eq = series[0][1]
            day_series = [e for t, e in series if t >= day_start]
            day_open = day_series[0] if day_series else eq
            bullets.append(
                f"💰 Equity {eq:,.2f} ({_pct(eq, day_open)} today, {_pct(eq, start_eq)} since start, started {start_eq:,.0f})"
            )
        else:
            bullets.append("💰 No portfolio snapshots yet - has the engine ticked?")

        if state:
            open_pos = sum(len(s.open_symbols()) for s in state.sleeves.values())
            bullets.append(
                f"📊 {open_pos} open positions across {len(state.sleeves)} strategies, cash {state.total_cash():,.2f}"
            )
            if state.halted_reason:
                bullets.append(
                    f"🛑 HALTED: {state.halted_reason} - run `trading-agent resume` after review"
                )

        fills_today = [
            Fill.model_validate(ev.payload) for ev in self.store.read("fill", since=day_start)
        ]
        fees_today = sum(f.fee_quote for f in fills_today)
        trips_today = round_trips(
            [
                Fill.model_validate(ev.payload)
                for ev in self.store.read("fill", since=day_start - timedelta(days=3))
            ]
        )
        closed_today = [t for t in trips_today]
        pnl_today = sum(t.pnl for t in closed_today)
        wins = sum(1 for t in closed_today if t.pnl > 0)
        if fills_today or closed_today:
            wr = f", win rate {wins}/{len(closed_today)}" if closed_today else ""
            bullets.append(
                f"🔁 {len(fills_today)} fills today, closed-trade PnL {pnl_today:+,.2f}{wr}; fees {fees_today:,.2f}"
            )
        else:
            bullets.append("🔁 No trades today")

        best, worst = self._best_worst(day_start)
        if best and worst and best != worst:
            bullets.append(
                f"🏆 Best {best[0]} {best[1]:+.2f}% · worst {worst[0]} {worst[1]:+.2f}% today"
            )

        listings = [ev.payload["symbol"] for ev in self.store.read("new_listing", since=day_start)]
        if listings:
            bullets.append(
                f"🆕 {len(listings)} new listings: {', '.join(listings[:5])}{'…' if len(listings) > 5 else ''}"
            )

        evo = list(self.store.read("evolution", since=day_start))
        applied = [e for e in evo if e.payload["outcome"] in ("applied", "promoted")]
        rejected = [
            e
            for e in evo
            if e.payload["outcome"] in ("rejected", "rolled_back", "demoted", "error")
        ]
        for e in applied[:2]:
            p = e.payload
            bullets.append(
                f"🧬 {p['outcome']}: {p['kind']} {p['strategy_id']} - {_short(p.get('hypothesis') or p['reason'], 110)}"
            )
        if rejected:
            p = rejected[-1].payload
            bullets.append(
                f"🚫 {len(rejected)} evolution rejected; last: {p['kind']} {p['strategy_id']} - {_short(p['reason'], 100)}"
            )
        if not evo:
            bullets.append("🧬 No evolution run today")

        breakers = list(self.store.read("circuit_breaker", since=day_start))
        if breakers:
            names = Counter(b.payload["name"] for b in breakers)
            bullets.append(
                "⚠️ Breakers tripped: " + ", ".join(f"{k} x{v}" for k, v in names.items())
            )

        failed_jobs = [
            j for j in self.store.read("job_run", since=day_start) if not j.payload["ok"]
        ]
        if failed_jobs:
            bullets.append(
                f"❗ {len(failed_jobs)} job failures; last: {_short(failed_jobs[-1].payload.get('error') or '', 90)}"
            )

        reasons = Counter(
            ev.payload["reason"]
            for ev in self.store.read("decision", since=day_start)
            if not ev.payload["approved"]
        )
        if reasons:
            top, n = reasons.most_common(1)[0]
            bullets.append(f"🔎 Top rejection: {top} ({n}x) - {_explain(top)}")

        return bullets[: self.s.report.max_bullets]

    def format(self, bullets: list[str], now: datetime) -> str:
        tz = ZoneInfo(self.s.report.timezone)
        head = f"📈 trading-agent daily · {now.astimezone(tz).strftime('%a %d %b %H:%M')} · paper"
        return head + "\n" + "\n".join(f"• {b}" for b in bullets)

    def _best_worst(
        self, since: datetime
    ) -> tuple[tuple[str, float] | None, tuple[str, float] | None]:
        rets: list[tuple[str, float]] = []
        for entry in self.registry.active_entries():
            s = sleeve_equity_series(self.store, entry.id, since=since)
            if len(s) >= 2 and s[0][1] > 0:
                rets.append((entry.id, (s[-1][1] - s[0][1]) / s[0][1] * 100))
        if not rets:
            return None, None
        rets.sort(key=lambda x: x[1])
        return rets[-1], rets[0]


def _pct(now: float, ref: float) -> str:
    return f"{(now - ref) / ref * 100:+.2f}%" if ref else "n/a"


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


_EXPLAIN = {
    "edge_below_cost": "signals too weak to cover fees+slippage",
    "daily_loss_limit": "daily loss breaker blocked new entries",
    "daily_fee_budget": "fee budget breaker blocked new entries",
    "max_positions": "sleeve already at max open positions",
    "already_in_position": "no pyramiding",
    "below_min_order": "sleeve cash too small for exchange minimum",
    "trade_rate_limit": "per-day trade cap reached",
    "no_position": "sell signal without a position",
}


def _explain(reason: str) -> str:
    return _EXPLAIN.get(reason.split(":")[0], reason)
