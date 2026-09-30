"""Persistent engine state. One JSON file, written atomically after every tick."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from trading_agent.core.models import Sleeve


class DayStats(BaseModel):
    date: str  # YYYY-MM-DD in UTC
    start_equity: float
    fees_paid: float = 0.0
    trades_by_sleeve: dict[str, int] = Field(default_factory=dict)
    daily_loss_tripped: bool = False
    fee_budget_tripped: bool = False


class EngineState(BaseModel):
    started_at: datetime
    sleeves: dict[str, Sleeve] = Field(default_factory=dict)
    reserve_cash: float = 0.0
    peak_equity: float = 0.0
    day: DayStats | None = None
    halted_reason: str | None = Field(
        default=None, description="Set by the drawdown breaker; cleared by `trading-agent resume`"
    )
    liquidating: list[str] = Field(default_factory=list, description="Sleeve ids being wound down")
    last_tick: datetime | None = None
    ticks: int = 0

    def total_cash(self) -> float:
        return self.reserve_cash + sum(s.cash for s in self.sleeves.values())


class EngineStateRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> EngineState | None:
        if not self.path.exists():
            return None
        return EngineState.model_validate_json(self.path.read_text())

    def save(self, state: EngineState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2))
        tmp.replace(self.path)
